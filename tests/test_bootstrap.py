import time
from pathlib import Path

from fastapi.testclient import TestClient

from my_bank_api import bootstrap, config
from my_bank_api.config import Settings
from my_bank_api.main import create_app


def _settings(tmp_path, **kw):
    return Settings(data_dir=tmp_path / "data", backup_dir=tmp_path / "backups", jobs_enabled=False, **kw)


def _status(client, token):
    return client.get("/api/v1/accounts", headers={"Authorization": f"Bearer {token}"}).status_code


def test_env_token_is_accepted_and_replaced_when_it_changes(tmp_path):
    with TestClient(create_app(_settings(tmp_path, api_token="mb_premier"))) as client:
        assert _status(client, "mb_premier") == 200
    with TestClient(create_app(_settings(tmp_path, api_token="mb_second"))) as client:
        assert _status(client, "mb_second") == 200
        assert _status(client, "mb_premier") == 401
    # Réglage retiré (option de l'add-on vidée, variable supprimée) : le jeton est révoqué.
    with TestClient(create_app(_settings(tmp_path))) as client:
        assert _status(client, "mb_second") == 401


def test_backup_dir_defaults_to_data_in_addon(monkeypatch):
    monkeypatch.delenv("MYBANK_BACKUP_DIR", raising=False)
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    assert str(Settings.from_env().backup_dir) == "/backups"
    monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
    assert str(Settings.from_env().backup_dir) == "/data/backups"


def _wait_for(calls):
    """L'annonce tourne dans un fil d'exécution à part : on attend qu'elle ait eu lieu."""
    deadline = time.monotonic() + 5
    while not any(c[1] == "/discovery" for c in calls) and time.monotonic() < deadline:
        time.sleep(0.01)


def test_addon_creates_a_token_and_announces_it(tmp_path, monkeypatch):
    calls = []

    def fake_supervisor(method, path, body=None):
        calls.append((method, path, body))
        return {"result": "ok", "data": {"hostname": "abcd1234-my-bank-api"}}

    monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
    monkeypatch.setattr(bootstrap, "supervisor", fake_supervisor)
    with TestClient(create_app(_settings(tmp_path))):
        _wait_for(calls)
    token = (tmp_path / "data" / bootstrap.ADDON_TOKEN_FILE).read_text()
    assert calls[-1] == (
        "POST",
        "/discovery",
        {"service": "my_bank", "config": {"host": "abcd1234-my-bank-api", "port": 8000, "token": token}},
    )
    # Second démarrage : même jeton, toujours valable.
    calls.clear()
    with TestClient(create_app(_settings(tmp_path))) as client:
        _wait_for(calls)
        assert _status(client, token) == 200
    assert calls[-1][2]["config"]["token"] == token


def test_announce_gives_up_without_raising(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "x")

    def down(*args, **kwargs):
        raise OSError("supervisor injoignable")

    monkeypatch.setattr(bootstrap, "supervisor", down)
    assert bootstrap.announce("mb_x", attempts=2, delay=0) is False


def test_addon_option_api_token(tmp_path, monkeypatch):
    options = tmp_path / "options.json"
    options.write_text('{"api_token": "mb_option"}')
    monkeypatch.setattr(config, "Path", lambda p: options if p == "/data/options.json" else Path(p))
    monkeypatch.delenv("MYBANK_API_TOKEN", raising=False)
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    assert Settings.from_env().api_token is None  # hors add-on, options.json est ignoré
    monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
    assert Settings.from_env().api_token == "mb_option"
    monkeypatch.setenv("MYBANK_API_TOKEN", "mb_env")
    assert Settings.from_env().api_token == "mb_env"
