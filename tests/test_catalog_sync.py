import asyncio
import json
import time

import httpx
import pytest

from app.catalog import Catalog
from app.config import Settings
from app.ekt import CatalogError, EktClient
from app.models import Product
from app.search import SearchIndex


def make_catalog(handler, **settings):
    config = Settings(_env_file=None, ekt_mode='live', **settings)
    client = EktClient(config, httpx.MockTransport(handler))
    return Catalog(config, client)


def pages(request):
    assert request.url.params.get('per_page', '100') == '100'
    page = int(request.url.params['page'])
    return httpx.Response(200, json={
        'page': page, 'per_page': 1, 'count': int(page <= 8),
        'items': [{'id': page, 'name': f'Product {page}', 'article': f'SKU-{page}_'}] if page <= 8 else [],
    })


@pytest.mark.asyncio
async def test_complete_catalog_beyond_five_pages_and_restart():
    catalog = make_catalog(pages)
    try:
        await catalog.load()
        assert len(catalog.products) == 8
        assert not catalog.partial
        assert catalog.pages_loaded == 9
        assert [p.id for p in await catalog.search('SKU-8')] == ['8']
        def offline(request):
            pytest.fail('A fresh persistent snapshot must not request the API')
        other = make_catalog(offline)
        try:
            assert [p.id for p in await other.search('SKU-8_')] == ['8']
        finally:
            await other.client.close()
    finally:
        await catalog.client.close()


@pytest.mark.asyncio
async def test_failed_sync_preserves_snapshot_and_resumes_pages():
    catalog = make_catalog(pages, catalog_concurrency=1)
    try:
        await catalog.load()
        original = catalog.store.path.read_bytes()
        visited = []
        async def failure(page):
            visited.append(page)
            if page == 4:
                raise CatalogError('Unavailable')
            return pages(httpx.Request('GET', f'https://ekt.kz/api/products?page={page}')).json()
        catalog.client.page = failure
        with pytest.raises(CatalogError):
            await catalog.refresh(force=True)
        assert catalog.store.path.read_bytes() == original
        assert len(catalog.products) == 8
        # A new synchronizer can reuse completed pages without touching the API.
        resumed = make_catalog(lambda r: pytest.fail('Expected resumable page cache'), catalog_concurrency=1)
        try:
            await resumed.refresh()
            assert len(resumed.products) == 8
        finally:
            await resumed.client.close()
    finally:
        await catalog.client.close()


@pytest.mark.asyncio
async def test_repeated_pages_are_not_published_as_complete():
    catalog = make_catalog(lambda r: httpx.Response(200, json={
        'items': [{'id': 1, 'name': 'Repeated'}], 'per_page': 1,
    }))
    try:
        with pytest.raises(CatalogError, match='повторил'):
            await catalog.load()
        assert not catalog.store.path.exists()
        assert not catalog.loaded_at
    finally:
        await catalog.client.close()


@pytest.mark.asyncio
async def test_page_limit_is_partial_and_never_saved_as_complete():
    catalog = make_catalog(pages, catalog_max_pages=3)
    try:
        await catalog.load()
        assert catalog.partial
        assert len(catalog.products) == 3
        assert not catalog.store.path.exists()
    finally:
        await catalog.client.close()


@pytest.mark.asyncio
async def test_stale_search_returns_fast_and_reports_refresh_failure():
    catalog = make_catalog(pages)
    try:
        await catalog.load()
        catalog.saved_at = time.time() - 100000
        async def failure(page):
            raise CatalogError('Unavailable')
        catalog.client.page = failure
        # Expire saved page checkpoints as well.
        for path in catalog.store.pages.glob('*.json'):
            data = json.loads(path.read_text('utf-8'))
            data['saved_at'] = 0
            path.write_text(json.dumps(data), encoding='utf-8')
        assert [p.id for p in await catalog.search('SKU-8')] == ['8']
        await catalog.refresh_task
        assert catalog.stale
        assert catalog.last_error == 'Unavailable'
        assert len(catalog.products) == 8
    finally:
        await catalog.client.close()


@pytest.mark.asyncio
async def test_corrupt_snapshot_is_rebuilt_and_source_isolated():
    catalog = make_catalog(pages)
    try:
        catalog.store.path.write_text('{broken', encoding='utf-8')
        await catalog.load()
        assert len(catalog.products) == 8
        other = make_catalog(pages, ekt_api_user='another-account')
        try:
            assert not other.restore()
        finally:
            await other.client.close()
        resized = make_catalog(pages, catalog_page_size=20)
        try:
            assert not resized.restore()
            assert resized.store.page(1) is None
        finally:
            await resized.client.close()
    finally:
        await catalog.client.close()


@pytest.mark.asyncio
async def test_resume_interrupted_initial_download_without_snapshot():
    visited = []
    def interrupted(request):
        page = int(request.url.params['page'])
        if page == 4:
            return httpx.Response(401)
        return pages(request)
    first = make_catalog(interrupted, catalog_concurrency=1)
    try:
        with pytest.raises(CatalogError):
            await first.load()
        assert not first.store.path.exists()
        def recovered(request):
            visited.append(int(request.url.params['page']))
            return pages(request)
        second = make_catalog(recovered, catalog_concurrency=1)
        try:
            await second.load()
            assert visited == [4, 5, 6, 7, 8, 9]
            assert len(second.products) == 8
            assert not second.partial
        finally:
            await second.client.close()
    finally:
        await first.client.close()


@pytest.mark.asyncio
async def test_first_start_failure_does_not_block_chat_or_retry_every_query():
    catalog = make_catalog(lambda r: httpx.Response(401))
    try:
        catalog.schedule_refresh()
        await catalog.refresh_task
        failed_task = catalog.refresh_task
        with pytest.raises(CatalogError, match='ещё не загружен'):
            await asyncio.wait_for(catalog.search('SKU-8'), timeout=0.5)
        assert catalog.refresh_task is failed_task
    finally:
        await catalog.client.close()


def test_search_all_terms_exact_articles_and_electrical_units():
    products = {p.id: p for p in (
        Product(id='101', article='SKU-C16_', name='Автоматический выключатель C16 16А IEK'),
        Product(id='102', article='SKU-C160_', name='Автоматический выключатель C160 160А IEK'),
        Product(id='103', article='SKU-C16-OTHER', name='Автоматический выключатель C16 16А Legrand'),
        Product(id='104', article='WIRE', name='Кабель ВВГнг 3х2,5'),
        Product(id='105', article='DRX', name='АВ DRX 16А IEK'),
    )}
    index = SearchIndex(products)
    def ids(query):
        return {p.id for p in index.search(query, products)}
    assert ids('Есть ли артикул SKU-C16?') == {'101'}
    assert ids('SKU-C16_') == {'101'}
    assert ids('101') == {'101'}
    assert ids('автомат 16 А ИЭК') == {'101', '105'}
    assert ids('Подберите пожалуйста автомат на 16 ампер ИЭК') == {'101', '105'}
    assert ids('автомат C16 Legrand') == {'103'}
    assert ids('кабель ВВГнг 3x2.5') == {'104'}
    assert ids('автомат 999А') == set()
    assert ids('SKU-MISSING-987') == set()
    assert ids('найди пожалуйста') == set()
    assert ids('') == set()


def test_real_catalog_abbreviations_use_category_and_spaced_dimensions():
    products = {p.id: p for p in (
        Product(id='1', article='BREAKER', name='ВА47-29 (1ф) 16А IEK',
                category='nizkovoltnaya_apparatura/modulnye_avtomaticheskie_vyklyuchateli'),
        Product(id='2', article='CABLE', name='ВВГнг(А)-LS 3х 2,5 ГОСТ EKT',
                category='kabel_provod/kabel_silovoy_dlya_statsionarnoy_prokladki_/mednyy_vvgng_ls'),
    )}
    index = SearchIndex(products)
    assert [p.id for p in index.search('автомат 16 А ИЭК', products)] == ['1']
    assert [p.id for p in index.search('кабель ВВГнг 3x2.5', products)] == ['2']
    assert index.search('кабель ВВГнг 3x25', products) == []
