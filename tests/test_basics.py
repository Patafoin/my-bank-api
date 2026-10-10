from decimal import Decimal

import pytest

from my_bank_api.money import from_cents, parse_amount, to_cents


def test_health_without_token(anon):
    r = anon.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.parametrize("header", [None, "Bearer mauvais", "Basic abc"])
def test_token_required(anon, header):
    headers = {"Authorization": header} if header else {}
    assert anon.get("/api/v1/accounts", headers=headers).status_code == 401


def test_openapi_available(anon):
    assert anon.get("/openapi.json").status_code == 200


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("12,34", "12.34"), ("-14.8", "-14.80"), (" 1 234,50 ", "1234.50"), (7, "7.00"), (0.1, "0.10")],
)
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", ["12.345", "abc", "", True, None, "NaN"])
def test_parse_amount_rejects(raw):
    with pytest.raises(ValueError):
        parse_amount(raw)


def test_cents_roundtrip():
    assert to_cents(Decimal("-14.80")) == -1480
    assert from_cents(-1480) == Decimal("-14.80")


def test_payment_methods_seeded(client):
    names = [m["name"] for m in client.get("/api/v1/payment-methods").json()]
    assert names == ["Carte bancaire", "Virement", "Chèque", "Prélèvement", "Versement"]
