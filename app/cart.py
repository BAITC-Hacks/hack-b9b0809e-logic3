import asyncio
import secrets
import time
from dataclasses import dataclass, field
from decimal import Decimal
from app.catalog import Catalog
from app.models import ProposalRequest, Product


class CartError(Exception):
    pass


@dataclass
class Proposal:
    id: str
    product: Product
    quantity: Decimal
    expires: float


@dataclass
class Session:
    user_id: str | None = None
    csrf: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    touched: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    cart: dict[str, dict] = field(default_factory=dict)
    pending: Proposal | None = None
    history: list[dict] = field(default_factory=list)
    completed: dict[str, dict] = field(default_factory=dict)


class CartService:
    def __init__(self, catalog: Catalog):
        self.catalog = catalog

    def view(self, session: Session) -> dict:
        return {'items': list(session.cart.values()), 'url': '/cart', 'demo_cart': True,
                'total': str(sum((Decimal(x['price']) * Decimal(x['quantity']) for x in session.cart.values()), Decimal(0)))}

    def remove(self, session: Session, product_id: str) -> dict:
        session.cart.pop(product_id, None)
        # A pending quote was calculated against the old cart state.
        session.pending = None
        return {'message': 'Товар удалён из корзины.', 'cart': self.view(session)}

    def decrement(self, session: Session, product_id: str) -> dict:
        item = session.cart.get(product_id)
        if item is None:
            raise CartError('Товара нет в корзине.')
        step = Decimal(item.get('minimum', '1'))
        remaining = Decimal(item['quantity']) - step
        if remaining <= 0:
            del session.cart[product_id]
        else:
            item['quantity'] = str(remaining)
        # Изменение количества делает любое старое предложение неактуальным.
        session.pending = None
        return {'message': 'Количество уменьшено.', 'cart': self.view(session)}

    def clear(self, session: Session) -> dict:
        session.cart.clear()
        session.pending = None
        return {'message': 'Корзина очищена.', 'cart': self.view(session)}

    async def increment(self, session: Session, product_id: str, *, confirmed: bool) -> dict:
        # Clicking + on an existing cart line is the customer's explicit instruction.
        # This route is never exposed to the LLM and still requires CSRF and a strict boolean.
        if confirmed is not True:
            raise CartError('Необходимо явное подтверждение клиента.')
        item = session.cart.get(product_id)
        if item is None:
            raise CartError('Товара нет в корзине.')
        product = await self.catalog.detail(product_id, fresh=True)
        step = Decimal(item.get('minimum', '1'))
        if product.price != Decimal(item['price']) or product.minimum != step:
            raise CartError('Цена или минимальная партия изменилась. Удалите товар и добавьте его заново.')
        self.validate(session, product, step)
        item['quantity'] = str(Decimal(item['quantity']) + step)
        session.pending = None
        return {'message': 'Количество увеличено.', 'cart': self.view(session)}

    async def propose(self, session: Session, request: ProposalRequest) -> dict:
        session.pending = None
        product = await self.catalog.detail(request.product_id, fresh=True)
        self.validate(session, product, request.quantity)
        proposal = Proposal(secrets.token_urlsafe(24), product, request.quantity, time.monotonic() + 300)
        session.pending = proposal
        return {'id': proposal.id, 'product': product.model_dump(mode='json'),
                'quantity': str(proposal.quantity), 'expires_in': 300,
                'message': f'Добавить {product.name}, {proposal.quantity} шт./м по {product.price} ₸? Подтвердите кнопкой или ответьте «Да, добавь».'}

    @staticmethod
    def validate(session: Session, product: Product, quantity: Decimal) -> None:
        if product.stock is None or product.price is None:
            raise CartError('Цена или остаток неизвестны. Добавление недоступно.')
        if product.warnings:
            raise CartError('В характеристиках есть конфликт. Обратитесь к менеджеру.')
        if quantity < product.minimum or quantity % product.minimum:
            raise CartError(f'Количество должно быть кратно {product.minimum}.')
        # Проверяем итог по позиции: несколько малых добавлений не должны обойти остаток.
        current = Decimal(session.cart.get(product.id, {}).get('quantity', '0'))
        if quantity + current > product.stock:
            raise CartError(f'Доступно {product.stock}; уже в корзине {current}. Запрошенное количество превышает остаток.')

    async def add_to_cart(self, session: Session, proposal_id: str, *, confirmed: bool) -> dict:
        """Caller holds session.lock. Model cannot call this method or mint consent."""
        if confirmed is not True:
            raise CartError('Необходимо явное подтверждение клиента.')
        if proposal_id in session.completed:
            # Повтор запроса после сетевого сбоя не должен повторно увеличить количество.
            return {'message': 'Это подтверждение уже обработано.', 'cart': self.view(session)}
        proposal = session.pending
        if proposal is None or not secrets.compare_digest(proposal.id, proposal_id):
            raise CartError('Нет соответствующего предложения в этой сессии.')
        if proposal.expires < time.monotonic():
            session.pending = None
            raise CartError('Предложение истекло. Выберите товар заново.')
        # За время подтверждения цена/остаток могли измениться; кэшу здесь не доверяем.
        product = await self.catalog.detail(proposal.product.id, fresh=True)
        self.validate(session, product, proposal.quantity)
        if product.price != proposal.product.price or product.minimum != proposal.product.minimum:
            # Согласие относится только к показанным условиям, а не к новой цене.
            session.pending = None
            raise CartError('Цена или минимальная партия изменились. Требуется новое подтверждение.')
        quantity = proposal.quantity + Decimal(session.cart.get(product.id, {}).get('quantity', '0'))
        session.cart[product.id] = {'product_id': product.id, 'article': product.article,
                                 'name': product.name, 'quantity': str(quantity), 'price': str(product.price),
                                 'minimum': str(product.minimum)}
        result = {'message': 'Товар добавлен в демонстрационную корзину.', 'cart': self.view(session)}
        session.completed[proposal.id] = {'done': True}
        if len(session.completed) > 100:
            del session.completed[next(iter(session.completed))]
        session.pending = None
        return result
