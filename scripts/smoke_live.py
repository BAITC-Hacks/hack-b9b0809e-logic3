"""Read-only real API check. Credentials come from env; never printed."""
import asyncio
from app.config import Settings
from app.ekt import EktClient, normalize


async def main() -> None:
    client = EktClient(Settings())
    try:
        page = await client.page(1)
        products = [normalize(item) for item in page['items']]
        product = await client.detail('515291')
        assert products and product.id == '515291'
        print(f'Live API OK: page size={len(products)}, stock known={product.stock is not None}, warnings={len(product.warnings)}')
    finally:
        await client.close()


if __name__ == '__main__':
    asyncio.run(main())
