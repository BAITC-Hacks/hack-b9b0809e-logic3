import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.config import Settings


@pytest.fixture
def client():
    # Even with a key configured, known commercial rules do not need a paid LLM call.
    with TestClient(create_app(Settings(_env_file=None, ekt_mode='demo', openai_api_key='test-key'))) as c:
        c.headers['x-csrf-token'] = c.get('/api/session').json()['csrf']
        yield c


@pytest.mark.parametrize('message', ['Какие условия оплаты?', 'Как платить?', 'Сколько стоит доставка?', 'Есть самовывоз?'])
def test_verified_terms_without_llm(client, message):
    r = client.post('/api/chat', json={'message': message})
    assert r.status_code == 200, r.text
    text = r.json()['message']
    assert 'Физлица' in text and 'Юрлица' in text and '48 часов' in text
    assert 'правила пересекаются' in text and 'ровно 15 000' in text
    assert r.json()['sources'][0]['checked_at'] == '2026-09-23'
    assert client.get('/api/cart').json()['items'] == []


def test_minimum_from_product_and_missing_value(client):
    r = client.post('/api/chat', json={'message': 'Минимальная партия DEMO-C16-B?'})
    assert r.status_code == 200
    assert 'Минимальная партия для DEMO-C16-B: 1' in r.json()['message']
    r = client.post('/api/chat', json={'message': 'Минимальная партия id demo-105?'})
    assert r.status_code == 200
    assert 'не указана в API' in r.json()['message']


def test_general_minimum_does_not_download_catalog(client):
    r = client.post('/api/chat', json={'message': 'Какая минимальная партия?'})
    assert r.status_code == 200
    assert 'зависит от товара' in r.json()['message']
    assert not client.app.state.catalog.loaded_at
