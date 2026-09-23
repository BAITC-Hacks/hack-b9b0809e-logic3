import time
import pytest
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app

PASSWORD = 'history test password 2026'


def session(client):
    result = client.get('/api/session')
    assert result.status_code == 200
    client.headers['x-csrf-token'] = result.json()['csrf']


def register(client, email='history@example.com'):
    session(client)
    result = client.post('/api/auth/register', json={'email': email, 'name': 'Тест истории', 'password': PASSWORD})
    assert result.status_code == 201, result.text
    client.headers['x-csrf-token'] = result.json()['csrf']


@pytest.fixture
def client():
    settings = Settings(_env_file=None, ekt_mode='demo', openai_api_key='')
    with TestClient(create_app(settings)) as c:
        session(c)
        yield c, settings


def test_guest_messages_are_not_persisted(client):
    c, _ = client
    assert c.post('/api/chat', json={'message': 'Условия оплаты?'}).status_code == 200
    assert c.get('/api/chat/history').status_code == 401
    register(c)
    assert c.get('/api/chat/history').json()['entries'] == []


def test_history_survives_restart_and_restores_llm_context(client):
    c, settings = client
    register(c)
    response = c.post('/api/chat', json={'message': 'DEMO-C16-B'})
    assert response.status_code == 200, response.text
    assert response.json()['history_saved'] is True
    auth = c.cookies['ekt_auth']
    with TestClient(create_app(settings)) as reopened:
        reopened.cookies.set('ekt_auth', auth)
        session(reopened)
        history = reopened.get('/api/chat/history').json()['entries']
        assert history[0]['message'] == 'DEMO-C16-B'
        assert history[0]['response']['products'][0]['id'] == 'demo-102'
        context = reopened.app.state.chat_history.context(auth)
        assert context[0] == {'role': 'user', 'content': 'DEMO-C16-B'}
        assert reopened.get('/api/cart').json()['items'] == []


def test_history_isolates_users_and_logout_login(client):
    c, _ = client
    register(c)
    c.post('/api/chat', json={'message': 'Оплата наличными?'})
    logout = c.post('/api/auth/logout', json={})
    c.headers['x-csrf-token'] = logout.json()['csrf']
    assert c.get('/api/chat/history').status_code == 401
    register(c, 'second@example.com')
    assert c.get('/api/chat/history').json()['entries'] == []
    logout = c.post('/api/auth/logout', json={})
    c.headers['x-csrf-token'] = logout.json()['csrf']
    login = c.post('/api/auth/login', json={'email': 'history@example.com', 'password': PASSWORD})
    c.headers['x-csrf-token'] = login.json()['csrf']
    assert c.get('/api/chat/history').json()['entries'][0]['message'] == 'Оплата наличными?'


def test_history_never_restores_confirmation_or_attachments(client):
    c, _ = client
    register(c)
    result = c.post('/api/chat', json={'message': 'Добавь demo-102 2'}).json()
    proposal = result['proposal']['id']
    history = c.get('/api/chat/history')
    assert proposal not in history.text
    assert 'proposal' not in history.json()['entries'][0]['response']
    assert c.get('/api/cart').json()['items'] == []
    c.post('/api/chat', json={'message': 'Оплата?', 'attachment_text': 'PRIVATE ATTACHMENT CONTENT'})
    assert 'PRIVATE ATTACHMENT CONTENT' not in c.get('/api/chat/history').text
    c.app.state.sessions.clear()
    session(c)
    assert c.post('/api/chat', json={'message': 'Да, добавь'}).status_code == 409


def test_clear_is_csrf_protected_and_scoped(client):
    c, _ = client
    register(c)
    c.post('/api/chat', json={'message': 'Оплата?'})
    assert c.post('/api/chat/history/clear', json={}, headers={'x-csrf-token': 'wrong'}).status_code == 403
    assert len(c.get('/api/chat/history').json()['entries']) == 1
    assert c.post('/api/chat/history/clear', json={}).status_code == 200
    assert c.get('/api/chat/history').json()['entries'] == []
    assert c.app.state.chat_history.context(c.cookies['ekt_auth']) == []


def test_expired_auth_cannot_read_or_append(client):
    c, _ = client
    register(c)
    with c.app.state.accounts.connect() as db:
        db.execute('UPDATE auth_sessions SET expires_at=?', (int(time.time()) - 1,))
    assert c.get('/api/chat/history').status_code == 401


def test_history_and_cart_clicks(client):
    c, _ = client
    register(c)
    for msg in ('Оплата?', 'Доставка?', 'Минимальная партия?'):
        assert c.post('/api/chat', json={'message': msg}).status_code == 200
    entries = c.get('/api/chat/history').json()['entries']
    assert [e['message'] for e in entries] == ['Оплата?', 'Доставка?', 'Минимальная партия?']
    quote = c.post('/api/cart/proposals', json={'product_id': 'demo-102', 'quantity': '2'}).json()
    body = {'proposal_id': quote['id'], 'confirmed': True}
    c.post('/api/cart/confirm', json=body)
    c.post('/api/cart/confirm', json=body)
    assert len(c.get('/api/chat/history').json()['entries']) == 5
    assert c.get('/api/cart').json()['items'][0]['quantity'] == '2'


def test_select_dialog_continues_only_its_context_and_invalidates_consent(client):
    c, _ = client
    register(c)
    first = c.post('/api/chat/conversations/new', json={}).json()['conversation']['id']
    c.post('/api/chat', json={'message': 'Оплата?'})
    quote = c.post('/api/cart/proposals', json={'product_id': 'demo-102', 'quantity': '2'}).json()
    second = c.post('/api/chat/conversations/new', json={}).json()['conversation']['id']
    assert c.post('/api/cart/confirm', json={'proposal_id': quote['id'], 'confirmed': True}).status_code == 409
    c.post('/api/chat', json={'message': 'Доставка?'})
    assert c.get('/api/chat/history').json()['conversation']['id'] == second
    selected = c.post('/api/chat/conversations/select', json={'conversation_id': first})
    assert selected.status_code == 200
    assert selected.json()['entries'][0]['message'] == 'Оплата?'
    context = c.app.state.chat_history.context(c.cookies['ekt_auth'], first)
    assert not any(m['content'] == 'Доставка?' for m in context)
    c.post('/api/chat', json={'message': 'Минимальная партия?'})
    assert len(c.get('/api/chat/history').json()['entries']) == 3
    # Stale tabs cannot append or confirm in the newly selected conversation.
    assert c.post('/api/chat', json={'message': 'Оплата?'}, headers={'x-conversation-id': second}).status_code == 409


def test_date_filter_and_messages_are_not_deleted_at_100(client):
    c, _ = client
    register(c)
    cid = c.post('/api/chat/conversations/new', json={}).json()['conversation']['id']
    store = c.app.state.chat_history
    for i in range(105):
        store.append(c.cookies['ekt_auth'], f'Вопрос {i}', {'message': 'Ответ'}, cid)
    first = c.get('/api/chat/history').json()
    assert len(first['entries']) == 50 and first['has_more']
    second = c.get('/api/chat/history', params={'before': first['entries'][0]['id']}).json()
    third = c.get('/api/chat/history', params={'before': second['entries'][0]['id']}).json()
    assert len(third['entries']) == 5 and not third['has_more']
    then = int(time.time()) - 3 * 86400
    with c.app.state.accounts.connect() as db:
        db.execute('UPDATE chat_exchanges SET created_at=? WHERE conversation_id=?', (then, cid))
    found = c.get('/api/chat/conversations', params={'start': then - 60, 'end': then + 60}).json()
    assert found['conversations'][0]['id'] == cid
    assert c.get('/api/chat/conversations', params={'start': then - 200, 'end': then - 100}).json()['conversations'] == []


def test_cross_account_conversation_ids_and_scoped_delete(client):
    c, _ = client
    register(c)
    first = c.post('/api/chat/conversations/new', json={}).json()['conversation']['id']
    second = c.post('/api/chat/conversations/new', json={}).json()['conversation']['id']
    c.post('/api/chat/history/clear', json={})
    assert [x['id'] for x in c.get('/api/chat/conversations').json()['conversations']] == [first]
    logout = c.post('/api/auth/logout', json={}); c.headers['x-csrf-token'] = logout.json()['csrf']
    register(c, 'intruder@example.com')
    assert c.get('/api/chat/history', params={'conversation_id': first}).status_code == 404
    assert c.post('/api/chat/conversations/select', json={'conversation_id': first}).status_code == 404


def test_flat_history_migration_preserves_dates_and_is_idempotent(tmp_path):
    from app.accounts import AccountStore, RegisterInput
    from app.chat_history import ChatHistory
    store = AccountStore(str(tmp_path / 'legacy.sqlite3'), 3600)
    store.initialize()
    user, token = store.register(RegisterInput(email='legacy@example.com', name='История', password=PASSWORD))
    with store.connect() as db:
        db.execute('CREATE TABLE chat_exchanges(id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT NOT NULL,created_at INTEGER NOT NULL,message TEXT NOT NULL,response TEXT NOT NULL)')
        db.execute('INSERT INTO chat_exchanges(user_id,created_at,message,response) VALUES (?,?,?,?)',
                   (user['id'], 12345, 'Старый вопрос', '{"message":"Ответ"}'))
    history = ChatHistory(store)
    history.initialize(); history.initialize()
    assert len(history.conversations(token)['conversations']) == 1
    result = history.page(token)
    assert result['conversation']['title'] == 'Предыдущая переписка'
    assert result['entries'][0]['created_at'] == 12345
