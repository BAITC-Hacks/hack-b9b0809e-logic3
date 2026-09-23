import asyncio
import json
import re
import time
import logging
from pathlib import Path
from collections.abc import Callable
from app.config import Settings
from app.ekt import EktClient, CatalogError, normalize, PROPERTY_LABELS
from app.models import Product
from app.catalog_store import CatalogStore
from app.search import SearchIndex

CRITICAL = ('NOMINALNYY_TOK', 'KOLICHESTVO_POLYUSOV', 'NOMINALNOE_NAPRYAZHENIE',
            'NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST', 'CHARACTERISTIC')


class Catalog:
    def __init__(self, settings: Settings, client: EktClient):
        self.settings, self.client = settings, client
        self.products: dict[str, Product] = {}
        self.details: dict[str, tuple[float, Product]] = {}
        self.loaded_at = 0.0
        self.partial = False
        self.lock = asyncio.Lock()
        self.store = CatalogStore(settings)
        self.index = SearchIndex({})
        self.pages_loaded = 0
        self.last_error: str | None = None
        self.saved_at = 0.0
        self.refresh_task: asyncio.Task | None = None
        self.last_attempt = 0.0

    def restore(self) -> bool:
        if self.settings.ekt_mode != 'live':
            return False
        snapshot = self.store.read(self.store.path)
        try:
            if not snapshot or snapshot.get('partial') is not False:
                return False
            products = {row['id']: Product.model_validate(row) for row in snapshot['products']}
            saved_at = float(snapshot['saved_at'])
            pages = int(snapshot['pages'])
            index = SearchIndex(products)
        except (KeyError, TypeError, ValueError):
            return False
        self.products, self.index = products, index
        self.saved_at, self.pages_loaded = saved_at, pages
        self.loaded_at = time.monotonic() - max(0, time.time() - saved_at)
        return True

    async def background_refresh(self) -> None:
        self.last_attempt = time.monotonic()
        try:
            await self.refresh()
        except (CatalogError, OSError) as exc:
            self.last_error = str(exc) if isinstance(exc, CatalogError) else 'Не удалось записать кэш каталога.'
            logging.getLogger(__name__).warning('Catalog refresh failed: %s', self.last_error)

    def schedule_refresh(self) -> None:
        if self.last_error and time.monotonic() - self.last_attempt < 60:
            return
        if self.refresh_task is None or self.refresh_task.done():
            self.refresh_task = asyncio.create_task(self.background_refresh())

    @property
    def stale(self) -> bool:
        return bool(self.loaded_at) and time.time() - self.saved_at >= self.settings.catalog_ttl_seconds

    async def load(self) -> None:
        if not self.loaded_at:
            self.restore()
        if self.loaded_at:
            if self.stale and self.settings.ekt_mode == 'live':
                self.schedule_refresh()
            return
        if self.refresh_task is not None:
            self.schedule_refresh()
            if self.last_error:
                raise CatalogError(self.last_error + ' Каталог ещё не загружен; повторите поиск позже.')
            raise CatalogError(f'Каталог загружается: обработано страниц {self.pages_loaded}. Повторите поиск чуть позже.')
        await self.refresh()

    async def refresh(self, force: bool = False, progress: Callable[[int, int], None] | None = None) -> None:
        async with self.lock:
            if not force and self.loaded_at and not self.stale:
                return
            products = {}
            partial = False
            self.pages_loaded = 0
            if self.settings.ekt_mode == 'demo':
                rows = json.loads((Path(__file__).parent.parent / 'data/demo.json').read_text('utf-8'))
                products = {str(row['id']): normalize(row) for row in rows}
            else:
                async def fetch(page: int) -> dict:
                    cached = None if force else await asyncio.to_thread(self.store.page, page)
                    if cached is not None:
                        return cached
                    data = await self.client.page(page)
                    await asyncio.to_thread(self.store.save_page, page, data)
                    return data

                # Bound upstream load. Only an empty/short page proves the end;
                # API's count describes this page, not the whole catalog.
                width = self.settings.catalog_concurrency
                expected_page_size: int | None = None
                finished = False
                page = 1
                while page <= self.settings.catalog_max_pages and not finished:
                    end = min(page + (1 if page == 1 else width), self.settings.catalog_max_pages + 1)
                    batch = await asyncio.gather(*(fetch(n) for n in range(page, end)), return_exceptions=True)
                    for n, data in zip(range(page, end), batch):
                        if isinstance(data, BaseException):
                            raise data
                        rows = data['items']
                        try:
                            per_page = int(data.get('per_page', 20))
                            if per_page < 1 or len(rows) > per_page or ('page' in data and int(data['page']) != n):
                                raise ValueError('Invalid pagination')
                            if expected_page_size is not None and per_page != expected_page_size:
                                raise ValueError('Page size changed during download')
                            expected_page_size = per_page
                            fresh = {str(row['id']): normalize(row) for row in rows}
                        except (KeyError, TypeError, ValueError) as exc:
                            raise CatalogError(f'Некорректные данные каталога на странице {n}.') from exc
                        if rows and fresh.keys() <= products.keys():
                            raise CatalogError(f'API повторил страницу {n}; полный каталог не подтверждён.')
                        products.update(fresh)
                        self.pages_loaded = n
                        if progress:
                            progress(n, len(products))
                        if len(rows) < per_page:
                            finished = True
                            break
                    page = end
                partial = not finished
                if partial and self.loaded_at and not self.partial:
                    raise CatalogError('Достигнут лимит страниц; сохранён предыдущий полный каталог.')
                if not partial:
                    await asyncio.to_thread(self.store.write, self.store.path, {
                        'saved_at': time.time(), 'pages': self.pages_loaded, 'partial': False,
                        'products': [p.model_dump(mode='json') for p in products.values()],
                    })
            index = await asyncio.to_thread(SearchIndex, products)
            self.products, self.partial = products, partial
            self.index = index
            self.loaded_at = time.monotonic()
            self.saved_at = time.time()
            self.last_error = None

    async def detail(self, product_id: str, fresh: bool = False) -> Product:
        if self.settings.ekt_mode == 'demo':
            await self.load()
            if product_id not in self.products:
                raise CatalogError('Товар не найден.')
            return self.products[product_id].model_copy(deep=True)
        cached = self.details.get(product_id)
        if not fresh and cached and time.monotonic() - cached[0] < self.settings.detail_ttl_seconds:
            return cached[1]
        product = await self.client.detail(product_id)
        if len(self.details) >= 2000:
            self.details.clear()
        self.details[product_id] = (time.monotonic(), product)
        return product

    async def search(self, query: str) -> list[Product]:
        await self.load()
        return self.index.search(query, self.products)

    async def analogs(self, product_id: str) -> list[dict]:
        source = await self.detail(product_id)
        await self.load()
        if source.warnings or not source.category:
            return []
        keys = [key for key in CRITICAL if source.properties.get(key)]
        if len(keys) < 3:
            return []
        candidates = [p for p in self.products.values() if p.id != source.id and p.category == source.category][:20]
        results = []
        def canonical(value: object) -> str:
            return re.sub(r'\s+', '', str(value).casefold()).replace('a', 'а').replace('v', 'в').replace(',', '.')
        for candidate in candidates:
            p = await self.detail(candidate.id)
            if p.warnings or p.stock is None or p.stock <= 0:
                continue
            if all(canonical(p.properties.get(k, '')) == canonical(source.properties[k]) for k in keys):
                results.append({'product': p.model_dump(mode='json'),
                                'reason': 'Совпадают категория и параметры: ' + ', '.join(f'{PROPERTY_LABELS[k]}: {source.properties[k]}' for k in keys) + '. Совместимость монтажа подтвердите с менеджером.'})
            if len(results) == 3:
                break
        return results
