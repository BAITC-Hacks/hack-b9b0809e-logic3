import asyncio
import secrets
import time
from contextlib import asynccontextmanager
from contextlib import suppress
from pathlib import Path
from typing import Annotated
from fastapi import FastAPI, Depends, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from app.agent import Agent
from app.cart import CartService, CartError, Session
from app.catalog import Catalog
from app.config import Settings
from app.ekt import EktClient, CatalogError
from app.models import ChatRequest, ProposalRequest, CartItemRequest, IncrementCartItemRequest, ConfirmRequest
from app.uploads import MAX_BYTES, extract_document, extract_image
from app.accounts import AccountError, AccountStore
from app.auth import account_router

ROOT = Path(__file__).parent.parent


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    client = EktClient(settings)
    catalog = Catalog(settings, client)
    cart = CartService(catalog)
    agent = Agent(catalog, cart)
    sessions: dict[str, Session] = {}
    accounts = AccountStore(settings.auth_db_path, settings.auth_ttl_seconds)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await asyncio.to_thread(accounts.initialize)
        if settings.ekt_mode == 'live':
            await asyncio.to_thread(catalog.restore)
            if not catalog.loaded_at or catalog.stale:
                catalog.schedule_refresh()
        try:
            yield
        finally:
            if catalog.refresh_task:
                catalog.refresh_task.cancel()
                with suppress(asyncio.CancelledError):
                    await catalog.refresh_task
            await client.close()
            await agent.http.aclose()

    app = FastAPI(title='HACKALEM · EKT Assistant', lifespan=lifespan)
    app.state.catalog, app.state.cart, app.state.sessions = catalog, cart, sessions
    app.state.accounts = accounts
    app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')

    @app.middleware('http')
    async def security(request: Request, call_next):
        # Browser mutations require a same-origin token; no wildcard CORS.
        if request.method == 'POST':
            try:
                size = int(request.headers.get('content-length', '0'))
            except ValueError:
                return JSONResponse({'detail': 'Некорректный Content-Length.'}, status_code=400)
            if size > MAX_BYTES + 65536:
                return JSONResponse({'detail': 'Запрос слишком большой.'}, status_code=413)
            if request.headers.get('sec-fetch-site') == 'cross-site':
                return JSONResponse({'detail': 'Межсайтовый запрос отклонён.'}, status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'self'; base-uri 'self'; form-action 'self'"
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(CatalogError)
    async def catalog_error(request: Request, exc: CatalogError):
        return JSONResponse({'detail': str(exc)}, status_code=503)

    @app.exception_handler(CartError)
    async def cart_error(request: Request, exc: CartError):
        return JSONResponse({'detail': str(exc)}, status_code=409)

    @app.exception_handler(AccountError)
    async def account_error(request: Request, exc: AccountError):
        return JSONResponse({'detail': exc.message}, status_code=exc.status,
                            headers={'Retry-After': '900'} if exc.status == 429 else None)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Pydantic's default input/context may include a submitted plaintext password.
        return JSONResponse({'detail': 'Проверьте введённые данные.', 'errors': [
            {'field': str(e['loc'][-1]), 'message': e['msg']} for e in exc.errors()
        ]}, status_code=422)

    def session(request: Request) -> Session:
        sid = request.cookies.get('ekt_session', '')
        s = sessions.get(sid)
        if s is None or time.monotonic() - s.touched > settings.session_ttl_seconds:
            sessions.pop(sid, None)
            raise HTTPException(401, 'Сессия истекла. Обновите страницу.')
        user = accounts.current(request.cookies.get('ekt_auth', ''))
        if s.user_id != (user['id'] if user else None):
            sessions.pop(sid, None)
            raise HTTPException(401, 'Аккаунт изменился или сеанс завершён. Обновите страницу.')
        if request.method == 'POST' and not secrets.compare_digest(request.headers.get('x-csrf-token', ''), s.csrf):
            raise HTTPException(403, 'Неверный CSRF-токен.')
        s.touched = time.monotonic()
        return s

    S = Annotated[Session, Depends(session)]

    def rotate_session(request: Request, response: Response, user_id: str | None) -> str:
        # Account boundaries discard guest/previous account chat and cart context.
        # Old CSRF/session tokens must not remain usable after login or logout.
        sessions.pop(request.cookies.get('ekt_session', ''), None)
        sid = secrets.token_urlsafe(32)
        sessions[sid] = Session(user_id=user_id)
        response.set_cookie('ekt_session', sid, httponly=True, secure=settings.cookie_secure,
                            samesite='strict', max_age=settings.session_ttl_seconds)
        return sessions[sid].csrf

    app.include_router(account_router(accounts, settings, session, rotate_session))

    @app.get('/api/session')
    async def start(request: Request, response: Response):
        user = await asyncio.to_thread(accounts.current, request.cookies.get('ekt_auth', ''))
        user_id = user['id'] if user else None
        now = time.monotonic()
        for key in list(sessions):
            if now - sessions[key].touched > settings.session_ttl_seconds:
                del sessions[key]
        sid = request.cookies.get('ekt_session', '')
        if sid in sessions and sessions[sid].user_id != user_id:
            del sessions[sid]
        if sid not in sessions:
            if len(sessions) >= 1000:
                raise HTTPException(503, 'Достигнут лимит демонстрационных сессий.')
            sid = secrets.token_urlsafe(32)
            sessions[sid] = Session(user_id=user_id)
        sessions[sid].touched = now
        response.set_cookie('ekt_session', sid, httponly=True, secure=settings.cookie_secure,
                            samesite='strict', max_age=settings.session_ttl_seconds)
        return {'csrf': sessions[sid].csrf, 'mode': settings.ekt_mode, 'user': user,
                'llm': bool(settings.openai_api_key.get_secret_value()), 'cart': cart.view(sessions[sid])}

    @app.get('/health')
    async def health():
        return {'status': 'ok', 'mode': settings.ekt_mode, 'catalog_loaded': bool(catalog.loaded_at),
                'loaded_products': len(catalog.products), 'partial_catalog': catalog.partial,
                'catalog_stale': catalog.stale, 'catalog_pages': catalog.pages_loaded,
                'catalog_syncing': bool(catalog.refresh_task and not catalog.refresh_task.done()),
                'catalog_error': catalog.last_error}

    @app.post('/api/chat')
    async def chat(body: ChatRequest, s: S):
        async with s.lock:
            try:
                return await agent.respond(body.message, body.attachment_text, s)
            except ValidationError as exc:
                raise HTTPException(422, 'Некорректные параметры инструмента.') from exc

    @app.get('/api/products')
    async def products(q: str, s: S):
        if len(q) > 500:
            raise HTTPException(422, 'Слишком длинный запрос.')
        return await agent.execute('search_products', {'query': q}, s)

    @app.get('/api/products/{product_id}')
    async def detail(product_id: str, s: S):
        return await agent.execute('get_product_details', {'product_id': product_id}, s)

    @app.get('/api/cart')
    async def cart_view(s: S):
        return cart.view(s)

    @app.post('/api/cart/proposals')
    async def propose(body: ProposalRequest, s: S):
        async with s.lock:
            return await cart.propose(s, body)

    @app.post('/api/cart/confirm')
    async def confirm(body: ConfirmRequest, s: S):
        async with s.lock:
            return await cart.add_to_cart(s, body.proposal_id, confirmed=body.confirmed)

    @app.post('/api/cart/remove')
    async def remove_cart_item(body: CartItemRequest, s: S):
        async with s.lock:
            return cart.remove(s, body.product_id)

    @app.post('/api/cart/decrement')
    async def decrement_cart_item(body: CartItemRequest, s: S):
        async with s.lock:
            return cart.decrement(s, body.product_id)

    @app.post('/api/cart/increment')
    async def increment_cart_item(body: IncrementCartItemRequest, s: S):
        async with s.lock:
            return await cart.increment(s, body.product_id, confirmed=body.confirmed)

    @app.post('/api/cart/clear')
    async def clear_cart(s: S):
        async with s.lock:
            return cart.clear(s)

    @app.post('/api/upload')
    async def upload(file: UploadFile, s: S):
        data = await file.read(MAX_BYTES + 1)
        await file.close()
        if len(data) > MAX_BYTES:
            raise HTTPException(413, 'Вложение больше 5 МБ.')
        try:
            if Path(file.filename or '').suffix.lower() in {'.jpg', '.jpeg'}:
                text = await extract_image(data, settings)
            else:
                text = await asyncio.to_thread(extract_document, file.filename or '', data)
            return {'text': text, 'notice': 'Извлечённый текст может содержать ошибки. Проверьте артикулы и количество.'}
        except Exception as exc:
            # Do not expose remote responses, secrets or document contents in logs.
            detail = str(exc) if isinstance(exc, ValueError) else 'Не удалось прочитать вложение. Проверьте формат файла или подключение ИИ.'
            raise HTTPException(422, detail) from exc

    @app.get('/')
    @app.get('/cart')
    @app.get('/profile')
    async def index():
        return FileResponse(ROOT / 'static/index.html')

    return app


app = create_app()
