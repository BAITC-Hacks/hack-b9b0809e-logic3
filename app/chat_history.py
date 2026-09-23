"""Account-owned conversations; saved messages never carry live cart consent."""
import json
import secrets
import sqlite3
import time
from app.accounts import AccountError, AccountStore, fingerprint


class ChatHistory:
    PAGE_SIZE = 50

    def __init__(self, accounts: AccountStore):
        self.accounts = accounts

    def initialize(self) -> None:
        with self.accounts.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS chat_conversations (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    title TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS conversations_user ON chat_conversations(user_id, updated_at);
                CREATE TABLE IF NOT EXISTS chat_exchanges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at INTEGER NOT NULL, message TEXT NOT NULL, response TEXT NOT NULL,
                    conversation_id TEXT REFERENCES chat_conversations(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS chat_by_user ON chat_exchanges(user_id, id);
            """)
            db.execute('BEGIN IMMEDIATE')
            if 'conversation_id' not in {r['name'] for r in db.execute('PRAGMA table_info(chat_exchanges)')}:
                db.execute('ALTER TABLE chat_exchanges ADD COLUMN conversation_id TEXT REFERENCES chat_conversations(id) ON DELETE CASCADE')
            # Keep the previous flat history as one conversation per owner.
            for row in db.execute('SELECT user_id,MIN(created_at) first,MAX(created_at) last FROM chat_exchanges WHERE conversation_id IS NULL GROUP BY user_id').fetchall():
                cid = secrets.token_hex(16)
                db.execute('INSERT INTO chat_conversations VALUES (?,?,?,?,?)',
                           (cid, row['user_id'], 'Предыдущая переписка', row['first'], row['last']))
                db.execute('UPDATE chat_exchanges SET conversation_id=? WHERE user_id=? AND conversation_id IS NULL', (cid, row['user_id']))
            db.execute('CREATE INDEX IF NOT EXISTS messages_conversation ON chat_exchanges(conversation_id,id)')

    @staticmethod
    def owner(db: sqlite3.Connection, token: str) -> str:
        row = db.execute('SELECT user_id FROM auth_sessions WHERE token_hash=? AND expires_at>?',
                         (fingerprint(token), int(time.time()))).fetchone()
        if row is None:
            raise AccountError(401, 'Войдите в аккаунт, чтобы открыть историю.')
        return row['user_id']

    @staticmethod
    def owned(db: sqlite3.Connection, user: str, cid: str) -> dict:
        row = db.execute('SELECT * FROM chat_conversations WHERE id=? AND user_id=?', (cid, user)).fetchone()
        if row is None:
            raise AccountError(404, 'Диалог не найден.')
        return dict(row)

    def resolve(self, db: sqlite3.Connection, user: str, cid: str | None) -> str | None:
        if cid:
            return self.owned(db, user, cid)['id']
        row = db.execute('SELECT id FROM chat_conversations WHERE user_id=? ORDER BY updated_at DESC,rowid DESC LIMIT 1', (user,)).fetchone()
        return row['id'] if row else None

    def create(self, token: str) -> dict:
        with self.accounts.connect() as db:
            user = self.owner(db, token)
            now, cid = int(time.time()), secrets.token_hex(16)
            db.execute('INSERT INTO chat_conversations VALUES (?,?,?,?,?)', (cid, user, 'Новый чат', now, now))
            return self.owned(db, user, cid)

    def conversations(self, token: str, offset: int = 0, start: int | None = None, end: int | None = None) -> dict:
        with self.accounts.connect() as db:
            user = self.owner(db, token)
            where, params = 'c.user_id=?', [user]
            if start is not None and end is not None:
                # Date means any exchange that day, not only creation.
                where += ' AND (c.created_at>=? AND c.created_at<? OR EXISTS (SELECT 1 FROM chat_exchanges e WHERE e.conversation_id=c.id AND e.created_at>=? AND e.created_at<?))'
                params += [start, end, start, end]
            rows = db.execute(f'SELECT c.* FROM chat_conversations c WHERE {where} ORDER BY c.updated_at DESC,c.rowid DESC LIMIT 31 OFFSET ?', (*params, offset)).fetchall()
            return {'conversations': [dict(row) for row in rows[:30]], 'has_more': len(rows) > 30}

    def page(self, token: str, cid: str | None = None, before: int | None = None, limit: int = PAGE_SIZE) -> dict:
        with self.accounts.connect() as db:
            user = self.owner(db, token)
            cid = self.resolve(db, user, cid)
            if not cid:
                return {'conversation': None, 'entries': [], 'has_more': False}
            rows = db.execute('SELECT id,created_at,message,response FROM chat_exchanges WHERE user_id=? AND conversation_id=? AND id<? ORDER BY id DESC LIMIT ?',
                              (user, cid, before or 9223372036854775807, limit + 1)).fetchall()
            return {'conversation': self.owned(db, user, cid),
                    'entries': [{**dict(row), 'response': json.loads(row['response'])} for row in reversed(rows[:limit])],
                    'has_more': len(rows) > limit}

    def read(self, token: str, cid: str | None = None) -> list[dict]:
        return self.page(token, cid)['entries']

    def append(self, token: str, message: str, response: dict, cid: str | None = None) -> str:
        safe = {key: response[key] for key in ('message', 'products', 'analogs', 'sources') if key in response}
        safe['message'] = str(safe.get('message', ''))[:24000]
        with self.accounts.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            user = self.owner(db, token)
            cid = self.resolve(db, user, cid)
            now = int(time.time())
            if cid is None:
                cid = secrets.token_hex(16)
                db.execute('INSERT INTO chat_conversations VALUES (?,?,?,?,?)', (cid, user, 'Новый чат', now, now))
            empty = not db.execute('SELECT 1 FROM chat_exchanges WHERE conversation_id=? LIMIT 1', (cid,)).fetchone()
            db.execute('INSERT INTO chat_exchanges(user_id,created_at,message,response,conversation_id) VALUES (?,?,?,?,?)',
                       (user, now, message[:6000], json.dumps(safe, ensure_ascii=False), cid))
            if empty:
                db.execute('UPDATE chat_conversations SET title=? WHERE id=?', (' '.join(message.split())[:80], cid))
            db.execute('UPDATE chat_conversations SET updated_at=? WHERE id=?', (now, cid))
            return cid

    def clear(self, token: str, cid: str | None = None) -> None:
        with self.accounts.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            user = self.owner(db, token)
            cid = self.resolve(db, user, cid)
            if cid:
                db.execute('DELETE FROM chat_exchanges WHERE user_id=? AND conversation_id=?', (user, cid))
                db.execute('DELETE FROM chat_conversations WHERE user_id=? AND id=?', (user, cid))

    def context(self, token: str, cid: str | None = None) -> list[dict]:
        return [message for entry in self.page(token, cid, limit=6)['entries'] for message in (
            {'role': 'user', 'content': entry['message']},
            {'role': 'assistant', 'content': entry['response']['message'][:8000]},
        )]
