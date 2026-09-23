"""Atomic catalog snapshots and resumable pages; never store API credentials."""
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from app.config import Settings


class CatalogStore:
    def __init__(self, settings: Settings):
        self.path = Path(settings.catalog_cache_path)
        self.pages = self.path.with_suffix('.pages')
        self.ttl = settings.catalog_ttl_seconds
        self.source = hashlib.sha256(
            f'{settings.ekt_api_base.rstrip("/")}:{settings.ekt_api_user}:{settings.catalog_page_size}'.encode()
        ).hexdigest()

    def read(self, path: Path) -> dict[str, Any] | None:
        try:
            data = json.loads(path.read_text('utf-8'))
            if data['version'] == 1 and data['source'] == self.source:
                return data
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def write(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {'version': 1, 'source': self.source, **payload}
        # A failed download or interrupted process cannot truncate the active snapshot.
        fd, temporary = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as handle:
                json.dump(data, handle, ensure_ascii=False, separators=(',', ':'))
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def page(self, number: int) -> dict[str, Any] | None:
        cached = self.read(self.pages / f'{number}.json')
        if cached:
            try:
                age = time.time() - float(cached['saved_at'])
                if 0 <= age < self.ttl and isinstance(cached['data']['items'], list):
                    return cached['data']
            except (KeyError, TypeError, ValueError):
                pass
        return None

    def save_page(self, number: int, data: dict[str, Any]) -> None:
        self.write(self.pages / f'{number}.json', {'saved_at': time.time(), 'data': data})
