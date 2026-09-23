import json
import httpx
import pytest
from app.agent import Agent
from app.catalog import Catalog
from app.cart import CartService, CartError, Session
from app.config import Settings
from app.ekt import EktClient


@pytest.mark.asyncio
async def test_llm_can_only_propose_and_cannot_invent_facts():
    settings = Settings(_env_file=None, ekt_mode='demo', openai_api_key='test-key')
    client = EktClient(settings)
    catalog = Catalog(settings, client)
    cart = CartService(catalog)
    agent = Agent(catalog, cart)
    session = Session()
    def handler(request):
        body = json.loads(request.content)
        assert 'add_to_cart' not in [tool['function']['name'] for tool in body['tools']]
        return httpx.Response(200, json={'choices': [{'message': {
            'role': 'assistant', 'content': 'Выдуманная цена: 1 тенге. Уже куплено!',
            'tool_calls': [{'id': 'call_test', 'type': 'function', 'function': {
                'name': 'prepare_cart_addition', 'arguments': json.dumps({'product_id': 'demo-102', 'quantity': '2'})}}]
        }}]})
    await agent.http.aclose()
    agent.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        result = await agent.respond('Добавь 2 штуки demo-102', '', session)
        assert 'proposal' in result
        assert '2450' in result['message']
        assert 'Уже куплено' not in result['message']
        assert cart.view(session)['items'] == []
        with pytest.raises(CartError):
            await agent.execute('add_to_cart', {'confirmed': True}, session)
    finally:
        await client.close()
        await agent.http.aclose()
