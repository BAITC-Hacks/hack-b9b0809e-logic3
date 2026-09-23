import pytest


@pytest.fixture(autouse=True)
def isolated_catalog_cache(tmp_path, monkeypatch):
    # Mocked catalogs must never read or overwrite a developer's live snapshot.
    monkeypatch.setenv('CATALOG_CACHE_PATH', str(tmp_path / 'catalog.json'))
    monkeypatch.setenv('AUTH_DB_PATH', str(tmp_path / 'accounts.sqlite3'))
