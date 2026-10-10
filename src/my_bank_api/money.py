from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from pydantic import BeforeValidator, PlainSerializer, WithJsonSchema

from .i18n import tr

CENT = Decimal("0.01")


def parse_amount(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(tr("montant invalide"))
    try:
        if isinstance(value, Decimal):
            amount = value
        elif isinstance(value, int):
            amount = Decimal(value)
        elif isinstance(value, float):
            amount = Decimal(str(value))
        elif isinstance(value, str):
            amount = Decimal(value.strip().replace(" ", "").replace(" ", "").replace(",", "."))
        else:
            raise ValueError(tr("montant invalide"))
    except InvalidOperation as err:
        raise ValueError(tr("montant invalide")) from err
    if not amount.is_finite():
        raise ValueError(tr("montant invalide"))
    if amount != amount.quantize(CENT):
        raise ValueError(tr("deux décimales au maximum"))
    return amount.quantize(CENT)


def to_cents(amount: Decimal) -> int:
    return int(amount.quantize(CENT) * 100)


def from_cents(cents: int) -> Decimal:
    return (Decimal(cents) / 100).quantize(CENT)


Money = Annotated[
    Decimal,
    BeforeValidator(parse_amount),
    PlainSerializer(lambda d: f"{d:.2f}", return_type=str),
    WithJsonSchema({"type": "string", "pattern": r"^-?\d+([.,]\d{1,2})?$", "examples": ["-14.80"]}),
]
