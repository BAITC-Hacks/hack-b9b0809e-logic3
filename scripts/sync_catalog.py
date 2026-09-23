"""Download the entire public API catalog, resuming recent completed pages."""
import argparse
import asyncio
import sys
import time

from app.catalog import Catalog
from app.config import Settings
from app.ekt import CatalogError, EktClient


async def run(force: bool = False) -> None:
    settings = Settings(ekt_mode='live')
    if not settings.ekt_api_password.get_secret_value():
        raise CatalogError('Set EKT_API_PASSWORD in .env before synchronization.')
    client = EktClient(settings)
    catalog = Catalog(settings, client)
    started = time.monotonic()

    def progress(page: int, count: int) -> None:
        if page == 1 or page % 25 == 0:
            print(f'Pages: {page}; products: {count}; elapsed: {time.monotonic() - started:.1f}s', flush=True)

    try:
        await catalog.refresh(force=force, progress=progress)
        if catalog.partial:
            raise CatalogError('Page limit reached. Increase CATALOG_MAX_PAGES; complete snapshot was not replaced.')
        print(f'Complete: {len(catalog.products)} products, {catalog.pages_loaded} pages. Snapshot: {settings.catalog_cache_path}', flush=True)
    finally:
        await client.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--force', action='store_true', help='Fetch every page again instead of resuming cached pages.')
    args = parser.parse_args()
    try:
        asyncio.run(run(args.force))
    except (CatalogError, OSError) as exc:
        print(f'Synchronization failed: {exc}', file=sys.stderr)
        sys.exit(1)
