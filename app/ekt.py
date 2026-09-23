import asyncio
import re
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse
import httpx
from app.config import Settings
from app.models import Product


class CatalogError(Exception):
    pass


PROPERTY_LABELS = {
    'NOMINALNYY_TOK': 'Номинальный ток', 'KOLICHESTVO_POLYUSOV': 'Количество полюсов',
    'NOMINALNOE_NAPRYAZHENIE': 'Номинальное напряжение',
    'NOMINALNAYA_OTKLYUCHAYUSHCHAYA_SPOSOBNOST': 'Отключающая способность',
    'CHARACTERISTIC': 'Характеристика срабатывания', 'KRATNOST_MIN': 'Минимальная партия',
    'TORGOVAYA_MARKA': 'Производитель', 'TIP_USTANOVKI': 'Тип установки',
    'OBYEM': 'Тип изделия', 'ARTIKULPOSTAVSHCHIKA': 'Артикул производителя',
}


def number(value: Any) -> Decimal | None:
    if value is None or value == '':
        return None
    try:
        result = Decimal(str(value).replace(' ', '').replace(',', '.'))
        return result if result.is_finite() and result >= 0 else None
    except InvalidOperation:
        return None


def safe_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    if value.startswith('/') and not value.startswith('//'):
        value = 'https://ekt.kz' + value
    parsed = urlparse(value)
    return value if parsed.scheme == 'https' and parsed.hostname == 'ekt.kz' else None


def normalize(raw: dict[str, Any]) -> Product:
    props = raw.get('properties') or {}
    if not isinstance(props, dict):
        raise CatalogError('Неизвестный формат характеристик API.')
    url = safe_url(raw.get('url'))
    parts = urlparse(url or '').path.strip('/').split('/')
    warnings = []
    name = str(raw['name'])
    named_current = re.search(r'(?<![\w.])(\d+(?:[.,]\d+)?)\s*[аa](?!\w)', name, re.I)
    property_current = re.search(r'\d+(?:[.,]\d+)?', str(props.get('NOMINALNYY_TOK', '')))
    if named_current and property_current and number(named_current[1]) != number(property_current[0]):
        warnings.append('Конфликт номинального тока в названии и свойствах. Требуется проверка менеджером.')
    certificates = []
    for value in raw.get('certificates') or []:
        link = safe_url(value if isinstance(value, str) else value.get('url'))
        if link:
            certificates.append(link)
    for key, value in props.items():
        if any(tag in key.upper() for tag in ('CERT', 'SERT', 'СЕРТИФ')):
            for item in value if isinstance(value, list) else [value]:
                link = safe_url(item)
                if link:
                    certificates.append(link)
    return Product(
        id=str(raw['id']), article=str(raw.get('article') or raw['id']), name=name,
        category='/'.join(parts[1:-1]), price=number(raw.get('price')),
        stock=number(raw.get('quantity')), stores=raw.get('stores') or [],
        properties=props, description=str(raw.get('description') or ''),
        certificates=list(dict.fromkeys(certificates)), url=url, warnings=warnings,
        minimum=number(props.get('KRATNOST_MIN')) or Decimal('1'),
    )


class EktClient:
    """Only fixed API routes: untrusted product URLs never receive credentials."""
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.http = httpx.AsyncClient(
            base_url=settings.ekt_api_base.rstrip('/') + '/',
            auth=httpx.BasicAuth(settings.ekt_api_user, settings.ekt_api_password.get_secret_value()),
            timeout=httpx.Timeout(10, connect=5), transport=transport, follow_redirects=False,
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def get(self, route: str, params: dict[str, Any]) -> Any:
        for attempt in range(3):
            try:
                response = await self.http.get(route, params=params)
                if response.status_code == 404:
                    raise CatalogError('Товар не найден в API.')
                if response.status_code in (401, 403):
                    raise CatalogError('API отклонил авторизацию. Проверьте настройки сервера.')
                response.raise_for_status()
                return response.json()
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt == 2:
                    raise CatalogError('API каталога временно недоступен. Попробуйте позже.') from exc
            except httpx.HTTPStatusError as exc:
                if response.status_code < 500 and response.status_code != 429:
                    raise CatalogError('API вернул ошибку запроса.') from exc
                if attempt == 2:
                    raise CatalogError('API каталога временно недоступен.') from exc
            except ValueError as exc:
                raise CatalogError('API вернул некорректный JSON.') from exc
            await asyncio.sleep(0.2 * (2 ** attempt))

    async def page(self, page: int) -> dict[str, Any]:
        data = await self.get('products', {'page': page})
        if not isinstance(data, dict) or not isinstance(data.get('items'), list):
            raise CatalogError('Неизвестный формат списка товаров.')
        return data

    async def detail(self, product_id: str) -> Product:
        data = await self.get('products/detail', {'id': product_id})
        try:
            product = normalize(data)
            if product.id != product_id:
                raise ValueError('ID mismatch')
            return product
        except (KeyError, TypeError, ValueError) as exc:
            raise CatalogError('Неизвестный формат карточки товара.') from exc
