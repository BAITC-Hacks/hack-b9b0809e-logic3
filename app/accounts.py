"""Persistent accounts. No chat messages, cart contents or raw tokens are stored."""
import hashlib
import secrets
import sqlite3
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from collections.abc import Iterator
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator


class AccountError(Exception):
    def __init__(self, status: int, message: str) -> None:
        self.status, self.message = status, message
        super().__init__(message)


class ProfileInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=2, max_length=80)
    company: str = Field('', max_length=120)
    phone: str = Field('', max_length=30, pattern=r'^[+\d\s()\-]*$')
    city: str = Field('', max_length=80)

    @field_validator('name', 'company', 'phone', 'city', mode='before')
    @classmethod
    def clean_text(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = value.strip()
            if any(unicodedata.category(c).startswith('C') for c in value):
                raise ValueError('Уберите управляющие символы.')
        return value


class LoginInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    email: EmailStr = Field(max_length=254)
    password: SecretStr = Field(min_length=1, max_length=128)

    @field_validator('email', mode='before')
    @classmethod
    def normalize_email(cls, value: Any) -> Any:
        return value.strip().casefold() if isinstance(value, str) else value


class RegisterInput(LoginInput):
    name: str = Field(min_length=2, max_length=80)
    password: SecretStr = Field(min_length=12, max_length=128)

    @field_validator('name', mode='before')
    @classmethod
    def clean_name(cls, value: Any) -> Any:
        return ProfileInput.clean_text(value)


class PasswordInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    current_password: SecretStr = Field(min_length=1, max_length=128)
    new_password: SecretStr = Field(min_length=12, max_length=128)


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class AccountStore:
    def __init__(self, path: str, ttl: int) -> None:
        self.path, self.ttl = Path(path), ttl
        self.hasher = PasswordHasher()
        self.dummy_hash = ''

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL, password_hash TEXT NOT NULL,
                    company TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '',
                    city TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    expires_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS auth_sessions_user ON auth_sessions(user_id);
                CREATE TABLE IF NOT EXISTS auth_attempts (
                    key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires_at INTEGER NOT NULL
                );
                PRAGMA user_version=1;
            ''')
        # Unknown emails take the same expensive password verification path.
        self.dummy_hash = self.hasher.hash(secrets.token_urlsafe(32))

    @staticmethod
    def public(row: sqlite3.Row) -> dict[str, Any]:
        return {key: row[key] for key in ('id', 'email', 'name', 'company', 'phone', 'city', 'created_at')}

    def throttle(self, ip: str, identity: str, action: str) -> None:
        now = int(time.time())
        limits = [(f'{action}:ip:{ip}', 30), (f'{action}:identity:{identity}', 8)]
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM auth_attempts WHERE expires_at<=?', (now,))
            for key, limit in limits:
                row = db.execute('SELECT count FROM auth_attempts WHERE key=?', (fingerprint(key),)).fetchone()
                if row and row['count'] >= limit:
                    raise AccountError(429, 'Слишком много попыток. Повторите через 15 минут.')
            for key, _ in limits:
                db.execute('''INSERT INTO auth_attempts VALUES (?,1,?)
                    ON CONFLICT(key) DO UPDATE SET count=count+1''', (fingerprint(key), now + 900))

    def _issue(self, db: sqlite3.Connection, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        now = int(time.time())
        db.execute('DELETE FROM auth_sessions WHERE expires_at<=?', (now,))
        db.execute('INSERT INTO auth_sessions VALUES (?,?,?)', (fingerprint(token), user_id, now + self.ttl))
        return token

    def register(self, body: RegisterInput) -> tuple[dict, str]:
        encoded = self.hasher.hash(body.password.get_secret_value())
        try:
            with self.connect() as db:
                user_id = secrets.token_hex(16)
                db.execute('INSERT INTO users(id,email,name,password_hash,created_at) VALUES (?,?,?,?,?)',
                           (user_id, str(body.email), body.name, encoded, int(time.time())))
                token = self._issue(db, user_id)
                return self.public(db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()), token
        except sqlite3.IntegrityError as exc:
            raise AccountError(409, 'Не удалось создать аккаунт с этим email. Попробуйте войти.') from exc

    def verify(self, encoded: str, password: str) -> bool:
        try:
            return self.hasher.verify(encoded, password)
        except (VerificationError, InvalidHashError):
            return False

    def login(self, body: LoginInput) -> tuple[dict, str]:
        with self.connect() as db:
            row = db.execute('SELECT * FROM users WHERE email=?', (str(body.email),)).fetchone()
        encoded = row['password_hash'] if row else self.dummy_hash
        if not self.verify(encoded, body.password.get_secret_value()) or row is None:
            raise AccountError(401, 'Неверный email или пароль.')
        rehash = self.hasher.hash(body.password.get_secret_value()) if self.hasher.check_needs_rehash(encoded) else encoded
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # A concurrent password change must not mint a session with the old password.
            current = db.execute('SELECT * FROM users WHERE id=?', (row['id'],)).fetchone()
            if current['password_hash'] != encoded:
                raise AccountError(401, 'Пароль изменён. Войдите снова.')
            db.execute('UPDATE users SET password_hash=? WHERE id=?', (rehash, row['id']))
            return self.public(current), self._issue(db, row['id'])

    def current(self, token: str) -> dict | None:
        if not token or len(token) > 128:
            return None
        with self.connect() as db:
            row = db.execute('''SELECT u.* FROM users u JOIN auth_sessions s ON u.id=s.user_id
                WHERE s.token_hash=? AND s.expires_at>?''', (fingerprint(token), int(time.time()))).fetchone()
            return self.public(row) if row else None

    def require(self, token: str) -> dict:
        user = self.current(token)
        if not user:
            raise AccountError(401, 'Войдите в аккаунт, чтобы открыть профиль.')
        return user

    def logout(self, token: str) -> None:
        with self.connect() as db:
            db.execute('DELETE FROM auth_sessions WHERE token_hash=?', (fingerprint(token),))

    def update(self, token: str, body: ProfileInput) -> dict:
        user = self.require(token)
        with self.connect() as db:
            db.execute('UPDATE users SET name=?,company=?,phone=?,city=? WHERE id=?',
                       (body.name, body.company, body.phone, body.city, user['id']))
            return self.public(db.execute('SELECT * FROM users WHERE id=?', (user['id'],)).fetchone())

    def change_password(self, token: str, body: PasswordInput) -> tuple[dict, str]:
        user = self.require(token)
        with self.connect() as db:
            encoded = db.execute('SELECT password_hash FROM users WHERE id=?', (user['id'],)).fetchone()[0]
        if not self.verify(encoded, body.current_password.get_secret_value()):
            raise AccountError(400, 'Текущий пароль указан неверно.')
        if body.current_password.get_secret_value() == body.new_password.get_secret_value():
            raise AccountError(400, 'Новый пароль должен отличаться от текущего.')
        new_hash = self.hasher.hash(body.new_password.get_secret_value())
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            active = db.execute('SELECT 1 FROM auth_sessions WHERE token_hash=? AND expires_at>?',
                                (fingerprint(token), int(time.time()))).fetchone()
            if not active:
                raise AccountError(401, 'Сессия завершена. Войдите снова.')
            updated = db.execute('UPDATE users SET password_hash=? WHERE id=? AND password_hash=?',
                                 (new_hash, user['id'], encoded))
            if updated.rowcount != 1:
                raise AccountError(409, 'Пароль уже изменён. Войдите снова.')
            # Revoke every device, including this token, then issue a fresh cookie.
            db.execute('DELETE FROM auth_sessions WHERE user_id=?', (user['id'],))
            return user, self._issue(db, user['id'])
