from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.accounts import AccountError, AccountStore, RegisterInput, fingerprint
from app.config import Settings
from app.main import create_app

PASSWORD = 'a long test password 2026'


def fresh_session(client):
    response = client.get('/api/session')
    assert response.status_code == 200
    client.headers['x-csrf-token'] = response.json()['csrf']
    return response.json()


def register(client, email='alice@example.com', name='Алия'):
    response = client.post('/api/auth/register', json={'name': name, 'email': email, 'password': PASSWORD})
    assert response.status_code == 201, response.text
    client.headers['x-csrf-token'] = response.json()['csrf']
    return response


@pytest.fixture
def account_client():
    settings = Settings(_env_file=None, ekt_mode='demo', openai_api_key='')
    app = create_app(settings)
    with TestClient(app) as client:
        fresh_session(client)
        yield client, app, settings


def test_registration_normalization_hashes_and_cookie_flags(account_client):
    client, app, _ = account_client
    old_sid = client.cookies['ekt_session']
    response = register(client, '  ALICE@EXAMPLE.COM  ', ' Алия ')
    user = response.json()['user']
    assert user['email'] == 'alice@example.com' and user['name'] == 'Алия'
    assert 'password' not in response.text
    assert client.cookies['ekt_session'] != old_sid
    assert old_sid not in app.state.sessions
    assert any('HttpOnly' in cookie and 'SameSite=strict' in cookie for cookie in response.headers.get_list('set-cookie'))
    store = app.state.accounts
    with store.connect() as db:
        row = db.execute('SELECT password_hash FROM users').fetchone()
        assert row[0].startswith('$argon2id$') and PASSWORD not in row[0]
        assert store.verify(row[0], PASSWORD)
        token = db.execute('SELECT token_hash FROM auth_sessions').fetchone()[0]
        assert token == fingerprint(client.cookies['ekt_auth'])
        assert token != client.cookies['ekt_auth']
    assert client.get('/api/auth/me').json()['user']['id'] == user['id']


def test_csrf_and_cross_site_registration_are_blocked(account_client):
    client, app, _ = account_client
    data = {'name': 'Алия', 'email': 'alice@example.com', 'password': PASSWORD}
    assert client.post('/api/auth/register', json=data, headers={'x-csrf-token': 'bad'}).status_code == 403
    assert client.post('/api/auth/register', json=data, headers={'sec-fetch-site': 'cross-site'}).status_code == 403
    with app.state.accounts.connect() as db:
        assert db.execute('SELECT count(*) FROM users').fetchone()[0] == 0


@pytest.mark.parametrize('changes', [
    {'password': 'TooShort'}, {'password': 'z' * 129}, {'email': 'not-an-email'},
    {'name': '  '}, {'name': 'Алия\u0000'}, {'role': 'admin'},
])
def test_invalid_registration_does_not_echo_password(account_client, changes):
    client, _, _ = account_client
    data = {'name': 'Алия', 'email': 'alice@example.com', 'password': PASSWORD, **changes}
    response = client.post('/api/auth/register', json=data)
    assert response.status_code == 422
    assert data['password'] not in response.text


def test_duplicate_email_and_generic_login_failure(account_client):
    client, _, _ = account_client
    register(client)
    client.post('/api/auth/logout', json={})
    fresh_session(client)
    duplicate = client.post('/api/auth/register', json={'name': 'Другой', 'email': 'ALICE@EXAMPLE.COM', 'password': PASSWORD})
    assert duplicate.status_code == 409
    errors = []
    for email in ['alice@example.com', 'unknown@example.com']:
        response = client.post('/api/auth/login', json={'email': email, 'password': 'wrong password'})
        assert response.status_code == 401
        errors.append(response.json())
    assert errors[0] == errors[1]


def test_login_logout_rotate_context_and_invalidate_old_tokens(account_client):
    client, app, _ = account_client
    register(client)
    auth_token = client.cookies['ekt_auth']
    sid = client.cookies['ekt_session']
    csrf = client.headers['x-csrf-token']
    app.state.sessions[sid].history = [{'role': 'user', 'content': 'private'}]
    response = client.post('/api/auth/logout', json={})
    assert response.status_code == 200
    assert sid not in app.state.sessions
    assert app.state.accounts.current(auth_token) is None
    assert client.get('/api/auth/me').json()['user'] is None
    fresh_session(client)
    assert client.get('/api/cart').json()['items'] == []
    assert app.state.sessions[client.cookies['ekt_session']].history == []
    assert client.post('/api/auth/login', json={'email': 'alice@example.com', 'password': PASSWORD},
                       headers={'x-csrf-token': csrf}).status_code == 403
    response = client.post('/api/auth/login', json={'email': 'alice@example.com', 'password': PASSWORD})
    assert response.status_code == 200
    assert client.cookies['ekt_auth'] != auth_token


def test_profile_access_and_owner_fields_cannot_be_overridden(account_client):
    client, _, _ = account_client
    fields = {'name': 'Алия Ж.', 'company': 'ТОО Тест', 'phone': '+7 (777) 123-45-67', 'city': 'Алматы'}
    assert client.post('/api/auth/profile', json=fields).status_code == 401
    alice = register(client).json()['user']
    assert client.post('/api/auth/profile', json={**fields, 'id': 'another-user'}).status_code == 422
    assert client.post('/api/auth/profile', json={**fields, 'email': 'other@example.com'}).status_code == 422
    response = client.post('/api/auth/profile', json=fields)
    assert response.status_code == 200
    assert response.json()['user']['id'] == alice['id']
    for key, value in fields.items():
        assert client.get('/api/auth/me').json()['user'][key] == value


def test_accounts_and_sessions_survive_restart(account_client):
    client, _, settings = account_client
    user = register(client).json()['user']
    cookies = dict(client.cookies)
    with TestClient(create_app(settings)) as restarted:
        restarted.cookies.update(cookies)
        session = fresh_session(restarted)
        assert session['user']['id'] == user['id']
        assert restarted.get('/api/auth/me').json()['user']['email'] == 'alice@example.com'


def test_two_users_have_separate_profiles_and_history_schema(account_client):
    client, app, settings = account_client
    alice = register(client).json()['user']
    with TestClient(create_app(settings)) as bob:
        fresh_session(bob)
        bob_user = register(bob, 'bob@example.com', 'Боб').json()['user']
        assert bob_user['id'] != alice['id']
        assert bob.post('/api/auth/profile', json={'name': 'Новый Боб', 'city': 'Астана'}).status_code == 200
        assert client.get('/api/auth/me').json()['user']['name'] == 'Алия'
        assert client.get('/api/auth/me').json()['user']['city'] == ''
    with app.state.accounts.connect() as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables == {'users', 'auth_sessions', 'auth_attempts', 'chat_conversations', 'chat_exchanges', 'sqlite_sequence'}
        assert db.execute('SELECT count(*) FROM chat_exchanges').fetchone()[0] == 0


def test_password_change_requires_current_password_and_revokes_other_devices(account_client):
    client, app, settings = account_client
    register(client)
    first_token = client.cookies['ekt_auth']
    with TestClient(create_app(settings)) as other:
        fresh_session(other)
        response = other.post('/api/auth/login', json={'email': 'alice@example.com', 'password': PASSWORD})
        assert response.status_code == 200
        second_token = other.cookies['ekt_auth']
        new_password = 'my new secure password 2026'
        assert client.post('/api/auth/password', json={'current_password': 'wrong', 'new_password': new_password}).status_code == 400
        response = client.post('/api/auth/password', json={'current_password': PASSWORD, 'new_password': new_password})
        assert response.status_code == 200
        assert app.state.accounts.current(first_token) is None
        assert app.state.accounts.current(second_token) is None
        assert client.get('/api/auth/me').json()['user'] is not None
        assert other.get('/api/auth/me').json()['user'] is None
        fresh_session(other)
        assert other.post('/api/auth/login', json={'email': 'alice@example.com', 'password': PASSWORD}).status_code == 401
        assert other.post('/api/auth/login', json={'email': 'alice@example.com', 'password': new_password}).status_code == 200


def test_expired_auth_cannot_access_profile_or_old_context(account_client):
    client, app, _ = account_client
    register(client)
    with app.state.accounts.connect() as db:
        db.execute('UPDATE auth_sessions SET expires_at=0')
    assert client.get('/api/auth/me').json()['user'] is None
    assert client.post('/api/auth/profile', json={'name': 'Другой'}).status_code == 401
    assert fresh_session(client)['user'] is None


def test_login_rate_limit_survives_store_restart(account_client):
    client, _, settings = account_client
    for _ in range(8):
        response = client.post('/api/auth/login', json={'email': 'unknown@example.com', 'password': PASSWORD})
        assert response.status_code == 401
    response = client.post('/api/auth/login', json={'email': 'unknown@example.com', 'password': PASSWORD})
    assert response.status_code == 429 and response.headers['retry-after'] == '900'
    restarted = AccountStore(settings.auth_db_path, settings.auth_ttl_seconds)
    with pytest.raises(AccountError) as error:
        restarted.throttle('testclient', 'unknown@example.com', 'login')
    assert error.value.status == 429


def test_concurrent_duplicate_registration_creates_one_user(account_client):
    _, app, _ = account_client
    body = RegisterInput(name='Алия', email='race@example.com', password=PASSWORD)
    def attempt():
        try:
            app.state.accounts.register(body)
            return 201
        except AccountError as error:
            return error.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: attempt(), range(2))) == [201, 409]
    with app.state.accounts.connect() as db:
        assert db.execute('SELECT count(*) FROM users').fetchone()[0] == 1


def test_secure_cookie_configuration(tmp_path):
    settings = Settings(_env_file=None, ekt_mode='demo', cookie_secure=True, auth_db_path=str(tmp_path/'secure.sqlite3'))
    with TestClient(create_app(settings), base_url='https://testserver') as client:
        fresh_session(client)
        response = register(client)
        assert all('Secure' in cookie and 'HttpOnly' in cookie for cookie in response.headers.get_list('set-cookie'))
