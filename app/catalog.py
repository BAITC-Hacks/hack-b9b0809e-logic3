import asyncio
import json
import re
import time
from pathlib import Path
from app.config import Settings
from app.ekt import EktClient, CatalogError, normalize, PROPERTY_LABELS
from app.models import Product

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

    async def load(self) -> None:
        if self.loaded_at and time.monotonic() - self.loaded_at < self.settings.catalog_ttl_seconds:
            return
        async with self.lock:
            if self.loaded_at and time.monotonic() - self.loaded_at < self.settings.catalog_ttl_seconds:
                return
            products = {}
            partial = False
            if self.settings.ekt_mode == 'demo':
                rows = json.loads((Path(__file__).parent.parent / 'data/demo.json').read_text('utf-8'))
                products = {str(row['id']): normalize(row) for row in rows}
            else:
                for page in range(1, self.settings.catalog_max_pages + 1):
                    data = await self.client.page(page)
                    rows = data['items']
                    if not rows:
                        break
                    fresh = {str(row['id']): normalize(row) for row in rows}
                    if fresh.keys() <= products.keys():
                        partial = True
                        break
                    products.update(fresh)
                    if len(rows) < int(data.get('per_page', 20)):
                        break
                    partial = page == self.settings.catalog_max_pages
            self.products, self.partial = products, partial
            self.loaded_at = time.monotonic()

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
        tokens = re.findall(r'[\w-]+', query.casefold())
        stop = {'есть', 'ли', 'наличие', 'найди', 'покажи', 'товар', 'артикул', 'мне', 'нужен', 'нужны'}
        tokens = [t for t in tokens if t not in stop]
        def score(p: Product) -> int:
            exact = any(t in (p.id.casefold(), p.article.casefold()) for t in tokens)
            text = f'{p.name} {p.article} {p.category}'.casefold()
            return 1000 * exact + sum(len(t) for t in tokens if t in text)
        result = sorted(self.products.values(), key=score, reverse=True)
        return [p for p in result if score(p) > 0][:6]

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
