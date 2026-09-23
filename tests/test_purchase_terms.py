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


@pytest.mark.parametrize(('message', 'included', 'excluded'), [
    ('Какие условия оплаты?', 'Физлица', 'Доставка.'),
    ('Как платить?', 'Юрлица', 'Минимальная партия'),
    ('Можно заплатить наличкой?', 'Физлица', 'Доставка.'),
    ('Сколько стоит доставка?', '48 часов', 'Оплата.'),
    ('Есть самовывоз?', 'Самовывоз доступен', '48 часов'),
    ('Можно забрать самому?', 'Самовывоз доступен', 'Оплата.'),
    ('Можно поштучно?', 'зависит от товара', 'Оплата.'),
    ('Способы оплаты и доставки?', 'Доставка.', 'Минимальная партия'),
])
def test_verified_terms_without_llm(client, message, included, excluded):
    r = client.post('/api/chat', json={'message': message})
    assert r.status_code == 200, r.text
    text = r.json()['message']
    assert included in text and excluded not in text
    assert r.json()['sources'][0]['checked_at'] == '2026-09-23'
    assert client.get('/api/cart').json()['items'] == []


def test_general_conditions_include_all_topics(client):
    text = client.post('/api/chat', json={'message': 'Какие условия покупки?'}).json()['message']
    assert all(part in text for part in ['Оплата.', 'Доставка.', 'Минимальная партия'])


@pytest.mark.parametrize('message', [
    'Могу заплатить частями?', 'Есть Kaspi?', 'Доставите завтра к 9:00?',
    'Курьер привезёт ночью?', 'Можно вернуть товар?', 'Есть скидка для монтажников?',
])
def test_unverified_special_conditions_are_not_promised(client, message):
    response = client.post('/api/chat', json={'message': message})
    assert response.status_code == 200
    text = response.json()['message']
    assert 'подтвержден' in text.replace('ё', 'е') and 'менеджер' in text
    assert 'Физлица' not in text and '48 часов' not in text
    assert not client.app.state.catalog.loaded_at


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
