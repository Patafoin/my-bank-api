import pytest
from fastapi.testclient import TestClient

from my_bank_api.auth import create_token
from my_bank_api.config import Settings
from my_bank_api.main import create_app


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / "data", backup_dir=tmp_path / "backups", jobs_enabled=False)


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def anon(app):
    with TestClient(app) as client:
        yield client


@pytest.fixture
def client(anon, app):
    with app.state.sessionmaker() as session:
        token = create_token(session, "tests")
    anon.headers["Authorization"] = f"Bearer {token}"
    return anon


@pytest.fixture
def make(client):
    """Raccourcis de création qui vérifient le code retour."""

    class Make:
        def account(self, name="Compte courant", **kw):
            r = client.post("/api/v1/accounts", json={"name": name, **kw})
            assert r.status_code == 201, r.text
            return r.json()

        def category(self, name, parent_id=None, kind="expense", **kw):
            body = {"name": name, "parent_id": parent_id, **kw}
            if parent_id is None:
                body["kind"] = kind
            r = client.post("/api/v1/categories", json=body)
            assert r.status_code == 201, r.text
            return r.json()

        def tx(self, account_id, amount, date="2026-10-01", **kw):
            r = client.post("/api/v1/transactions", json={"account_id": account_id, "amount": amount, "date": date, **kw})
            assert r.status_code == 201, r.text
            return r.json()

    return Make()
