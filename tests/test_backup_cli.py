import datetime as dt
import sqlite3

from my_bank_api import cli
from my_bank_api.backup import rotate, run_backup
from my_bank_api.config import Settings
from my_bank_api.jobs import seconds_until


def test_backup_is_a_readable_database(client, settings, make):
    make.account("Livret A")
    path = run_backup(settings, dt.date(2026, 10, 6))
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM accounts").fetchall() == [("Livret A",)]


def test_rotation_keeps_recent_days_and_first_of_months(tmp_path):
    settings = Settings(data_dir=tmp_path, backup_dir=tmp_path, backup_keep_daily=3, backup_keep_monthly=2)
    days = [dt.date(2026, 8, 1) + dt.timedelta(days=i) for i in range(70)]
    for day in days:
        (tmp_path / f"my_bank-{day.isoformat()}.sqlite").touch()
    rotate(settings)
    kept = sorted(p.name for p in tmp_path.glob("my_bank-*.sqlite"))
    assert kept == [
        "my_bank-2026-09-01.sqlite",
        "my_bank-2026-10-01.sqlite",
        "my_bank-2026-10-07.sqlite",
        "my_bank-2026-10-08.sqlite",
        "my_bank-2026-10-09.sqlite",
    ]


def test_seconds_until():
    assert seconds_until(3, dt.datetime(2026, 10, 6, 2, 0)) == 3600
    assert seconds_until(3, dt.datetime(2026, 10, 6, 3, 0)) == 24 * 3600


def test_cli_tokens(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MYBANK_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MYBANK_BACKUP_DIR", str(tmp_path / "backups"))
    assert cli.main(["token", "create", "ha"]) == 0
    token = capsys.readouterr().out.strip()
    assert token.startswith("mb_") and len(token) > 40
    assert cli.main(["token", "create", "ha"]) == 1
    cli.main(["token", "list"])
    assert "ha\t" in capsys.readouterr().out
    assert cli.main(["token", "revoke", "ha"]) == 0
    assert cli.main(["token", "revoke", "ha"]) == 1
    assert cli.main(["backup"]) == 0
    assert list((tmp_path / "backups").glob("my_bank-*.sqlite"))
