from decimal import Decimal
from typing import Any
from pydantic import BaseModel, Field, ConfigDict


class Product(BaseModel):
    id: str
    article: str
    name: str
    category: str = ''
    price: Decimal | None = Field(None, ge=0)
    stock: Decimal | None = Field(None, ge=0)
    stores: list[dict[str, Any]] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)
    description: str = ''
    certificates: list[str] = Field(default_factory=list)
    url: str | None = None
    warnings: list[str] = Field(default_factory=list)
    minimum: Decimal = Field(Decimal('1'), gt=0)


class ProposalRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    product_id: str = Field(min_length=1, max_length=80, pattern=r'^[\w-]+$')
    quantity: Decimal = Field(gt=0, le=1000000, max_digits=12, decimal_places=3)


class CartItemRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    product_id: str = Field(min_length=1, max_length=80, pattern=r'^[\w-]+$')


class IncrementCartItemRequest(CartItemRequest):
    confirmed: bool = Field(strict=True)


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    proposal_id: str = Field(min_length=20, max_length=100)
    confirmed: bool = Field(strict=True)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message: str = Field(min_length=1, max_length=6000)
    attachment_text: str = Field('', max_length=16000)
