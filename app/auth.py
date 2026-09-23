"""Account endpoints; the LLM has no access to this router's operations."""
import asyncio
from collections.abc import Callable

from fastapi import APIRouter, Depends, Request, Response

from app.accounts import AccountError, AccountStore, LoginInput, PasswordInput, ProfileInput, RegisterInput
from app.config import Settings
from app.cart import Session


def account_router(
    store: AccountStore,
    settings: Settings,
    session_dependency: Callable[[Request], Session],
    rotate_session: Callable[[Request, Response, str | None], str],
) -> APIRouter:
    router = APIRouter(prefix='/api/auth', tags=['Accounts'])
    hashing_slots = asyncio.Semaphore(2)

    def token(request: Request) -> str:
        return request.cookies.get('ekt_auth', '')

    def set_cookie(response: Response, value: str) -> None:
        response.set_cookie('ekt_auth', value, max_age=settings.auth_ttl_seconds,
                            httponly=True, secure=settings.cookie_secure, samesite='strict', path='/')

    async def throttle(request: Request, identity: str, action: str) -> None:
        ip = request.client.host if request.client else 'unknown'
        await asyncio.to_thread(store.throttle, ip, identity, action)

    @router.get('/me')
    async def me(request: Request) -> dict:
        return {'user': await asyncio.to_thread(store.current, token(request))}

    @router.post('/register', status_code=201)
    async def register(body: RegisterInput, request: Request, response: Response, s: Session = Depends(session_dependency)) -> dict:
        async with s.lock:
            if s.user_id:
                raise AccountError(409, 'Вы уже вошли в аккаунт.')
            await throttle(request, str(body.email), 'register')
            async with hashing_slots:
                user, value = await asyncio.to_thread(store.register, body)
            set_cookie(response, value)
            csrf = rotate_session(request, response, user['id'])
            return {'user': user, 'csrf': csrf}

    @router.post('/login')
    async def login(body: LoginInput, request: Request, response: Response, s: Session = Depends(session_dependency)) -> dict:
        async with s.lock:
            if s.user_id:
                raise AccountError(409, 'Вы уже вошли в аккаунт.')
            await throttle(request, str(body.email), 'login')
            async with hashing_slots:
                user, value = await asyncio.to_thread(store.login, body)
            set_cookie(response, value)
            csrf = rotate_session(request, response, user['id'])
            return {'user': user, 'csrf': csrf}

    @router.post('/logout')
    async def logout(request: Request, response: Response, s: Session = Depends(session_dependency)) -> dict:
        async with s.lock:
            await asyncio.to_thread(store.logout, token(request))
            response.delete_cookie('ekt_auth', path='/', httponly=True,
                                   secure=settings.cookie_secure, samesite='strict')
            csrf = rotate_session(request, response, None)
            return {'user': None, 'csrf': csrf}

    @router.post('/profile')
    async def profile(body: ProfileInput, request: Request, s: Session = Depends(session_dependency)) -> dict:
        async with s.lock:
            return {'user': await asyncio.to_thread(store.update, token(request), body)}

    @router.post('/password')
    async def password(body: PasswordInput, request: Request, response: Response, s: Session = Depends(session_dependency)) -> dict:
        async with s.lock:
            user = await asyncio.to_thread(store.require, token(request))
            await throttle(request, user['id'], 'password')
            async with hashing_slots:
                user, value = await asyncio.to_thread(store.change_password, token(request), body)
            set_cookie(response, value)
            csrf = rotate_session(request, response, user['id'])
            return {'user': user, 'csrf': csrf, 'message': 'Пароль изменён. Остальные сеансы завершены.'}

    return router
