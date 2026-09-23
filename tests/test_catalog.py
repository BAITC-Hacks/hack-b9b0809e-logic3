import asyncio
import httpx
import pytest
from app.config import Settings
from app.ekt import EktClient, CatalogError, normalize
from app.catalog import Catalog
from app.cart import CartService, Session
from app.models import ProposalRequest


def test_actual_api_shape_and_conflict():
    p = normalize({'id': 515291, 'article': '200300285_', 'name': '027228 АВ DRX250 MT 3ф 160А 18ka Legrand',
                   'quantity': 23, 'price': 64920, 'properties': {'NOMINALNYY_TOK': '250 А'},
                   'certificates': ['https://ekt.kz/upload/certificate.pdf', 'javascript:alert(1)']})
    assert p.stock == 23
    assert p.warnings
    assert p.certificates == ['https://ekt.kz/upload/certificate.pdf']
    assert normalize({'id': 1, 'name': 'No stock'}).stock is None


@pytest.mark.asyncio
async def test_auth_pagination_cache_fresh_stock():
    calls = []
    def handler(request):
        calls.append(str(request.url))
        assert request.headers['authorization'].startswith('Basic ')
        if request.url.path.endswith('/detail'):
            return httpx.Response(200, json={'id': 1, 'name': 'Item', 'quantity': 3})
        page = request.url.params['page']
        return httpx.Response(200, json={'items': [{'id': 1, 'name': 'Item'}] if page == '1' else [], 'per_page': 1})
    s = Settings(_env_file=None, ekt_mode='live')
    client = EktClient(s, httpx.MockTransport(handler))
    catalog = Catalog(s, client)
    try:
        await asyncio.gather(catalog.load(), catalog.load())
        assert len(calls) == 2
        await catalog.search('Item')
        assert len(calls) == 2
        await catalog.detail('1')
        await catalog.detail('1')
        assert len(calls) == 3
        await catalog.detail('1', fresh=True)
        assert len(calls) == 4
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('code', [401, 429, 500])
async def test_api_errors(code):
    client = EktClient(Settings(_env_file=None), httpx.MockTransport(lambda r: httpx.Response(code)))
    try:
        with pytest.raises(CatalogError):
            await client.page(1)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_timeout_and_invalid_json():
    def timeout(request):
        raise httpx.ReadTimeout('timeout', request=request)
    for handler in (timeout, lambda r: httpx.Response(200, text='<html>')):
        client = EktClient(Settings(_env_file=None), httpx.MockTransport(handler))
        try:
            with pytest.raises(CatalogError):
                await client.page(1)
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_concurrent_confirmation():
    settings = Settings(_env_file=None, ekt_mode='demo')
    client = EktClient(settings)
    service = CartService(Catalog(settings, client))
    session = Session()
    p = await service.propose(session, ProposalRequest(product_id='demo-102', quantity=2))
    async def confirm():
        async with session.lock:
            return await service.add_to_cart(session, p['id'], confirmed=True)
    try:
        await asyncio.gather(confirm(), confirm())
        assert service.view(session)['items'][0]['quantity'] == '2'
    finally:
        await client.close()
