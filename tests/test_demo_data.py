import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "demo_data.py"


def _load():
    spec = importlib.util.spec_from_file_location("demo_data", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(("lang", "account", "method"), [("en", "Checking account", "Card"), ("fr", "Compte courant", "Carte bancaire"), ("de", "Girokonto", "Karte")])
def test_demo_data_loads_in_each_language(client, monkeypatch, lang, account, method):
    demo = _load()

    def call(self, method_name, path, body=None):
        r = client.request(method_name, f"/api/v1/{path}", json=body)
        assert r.status_code < 400, r.text
        return r.json() if r.content else None

    monkeypatch.setattr(demo.Api, "call", call)
    monkeypatch.setattr(sys, "argv", ["demo_data.py", "--url", "http://test", "--token", "x", "--lang", lang])
    demo.main()
    accounts = client.get("/api/v1/accounts").json()
    assert account in [a["name"] for a in accounts]
    assert method in [m["name"] for m in client.get("/api/v1/payment-methods").json()]
    assert client.get("/api/v1/schedules").json() and client.get("/api/v1/budgets").json()
