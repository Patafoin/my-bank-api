"""Profils (décision 028) : comptes, rubriques, classes et tiers séparés ; références contrôlées."""

import sqlite3

from alembic import command
from sqlalchemy import create_engine

from my_bank_api.db import make_engine
from my_bank_api.migrate import alembic_config, upgrade

API = "/api/v1"


def _profile(client, name):
    r = client.post(f"{API}/profiles", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


def test_default_profile_holds_everything(client, make):
    profiles = client.get(f"{API}/profiles").json()
    assert [(p["id"], p["is_default"]) for p in profiles] == [(1, True)]
    account = make.account()
    category = make.category("Courses")
    assert account["profile_id"] == category["profile_id"] == 1
    assert client.get(f"{API}/profiles").json()[0]["accounts"] == 1


def test_rename_create_and_delete(client, make):
    r = client.patch(f"{API}/profiles/1", json={"name": "Alice"})
    assert r.status_code == 200 and r.json()["name"] == "Alice"
    other = _profile(client, "Bruno")
    assert other["is_default"] is False and other["accounts"] == 0
    assert client.post(f"{API}/profiles", json={"name": "Bruno"}).status_code == 409
    make.account("Livret", profile_id=other["id"])
    r = client.delete(f"{API}/profiles/{other['id']}")
    assert r.status_code == 409  # pas vide
    account = client.get(f"{API}/accounts", params={"profile_id": other["id"]}).json()[0]
    assert client.delete(f"{API}/accounts/{account['id']}").status_code == 204
    assert client.delete(f"{API}/profiles/{other['id']}").status_code == 204
    assert client.delete(f"{API}/profiles/1").status_code == 409  # le dernier
    assert client.post(f"{API}/accounts", json={"name": "X", "profile_id": 99}).status_code == 404


def test_same_names_in_two_profiles_and_filters(client, make):
    other = _profile(client, "Bruno")["id"]
    a1 = make.account("Compte courant", is_default=True)
    a2 = make.account("Compte courant", profile_id=other, is_default=True)
    # Un compte par défaut par profil : le second n'a pas retiré le premier.
    assert client.get(f"{API}/accounts/{a1['id']}").json()["is_default"] is True
    assert a2["is_default"] is True
    assert client.post(f"{API}/accounts", json={"name": "Compte courant"}).status_code == 409
    for name in ("EDF",):
        assert client.post(f"{API}/payees", json={"name": name}).status_code == 201
        assert client.post(f"{API}/payees", json={"name": name, "profile_id": other}).status_code == 201
    make.category("Courses")
    make.category("Courses", profile_id=other)
    r = client.post(f"{API}/classes", json={"name": "Perso", "profile_id": other})
    assert r.status_code == 201 and r.json()["profile_id"] == other

    def names(path, profile_id):
        return [x["name"] for x in client.get(f"{API}/{path}", params={"profile_id": profile_id}).json()]

    assert names("accounts", other) == ["Compte courant"] and len(client.get(f"{API}/accounts").json()) == 2
    assert names("payees", other) == ["EDF"]
    assert names("categories", other) == ["Courses"]
    assert names("classes", 1) == [] and names("classes", other) == ["Perso"]

    make.tx(a1["id"], "-10")
    make.tx(a2["id"], "-20")
    page = client.get(f"{API}/transactions", params={"profile_id": other}).json()
    assert page["total"] == 1 and page["items"][0]["amount"] == "-20.00"


def test_subcategory_follows_its_parent(client, make):
    other = _profile(client, "Bruno")["id"]
    parent = make.category("Maison", profile_id=other)
    child = make.category("Travaux", parent_id=parent["id"])
    assert child["profile_id"] == other
    r = client.post(f"{API}/categories", json={"name": "X", "parent_id": parent["id"], "profile_id": 1})
    assert r.status_code == 422
    mine = make.category("Loisirs")
    r = client.patch(f"{API}/categories/{mine['id']}", json={"parent_id": parent["id"]})
    assert r.status_code == 422 and "autre profil" in r.json()["detail"]


def test_references_must_share_the_account_profile(client, make):
    other = _profile(client, "Bruno")["id"]
    mine = make.account("Courant")
    hers = make.account("Livret", profile_id=other)
    her_category = make.category("Courses", profile_id=other)
    her_payee = client.post(f"{API}/payees", json={"name": "EDF", "profile_id": other}).json()
    body = {"account_id": mine["id"], "date": "2026-10-01", "amount": "-5"}
    for extra in ({"category_id": her_category["id"]}, {"payee_id": her_payee["id"]}):
        r = client.post(f"{API}/transactions", json={**body, **extra})
        assert r.status_code == 422 and "autre profil" in r.json()["detail"], r.text

    # payee_name : cherché puis créé dans le profil du compte.
    tx = make.tx(hers["id"], "-5", payee_name="edf")
    assert tx["payee_id"] == her_payee["id"]
    tx = make.tx(mine["id"], "-5", payee_name="EDF")
    assert tx["payee_id"] != her_payee["id"]

    # Changer de compte vers un autre profil : refusé tant que le tiers reste celui de l'ancien.
    r = client.patch(f"{API}/transactions/{tx['id']}", json={"account_id": hers["id"]})
    assert r.status_code == 422
    r = client.patch(f"{API}/transactions/{tx['id']}", json={"account_id": hers["id"], "payee_id": None})
    assert r.status_code == 200, r.text

    r = client.post(f"{API}/transfers", json={"from_account_id": mine["id"], "to_account_id": hers["id"], "date": "2026-10-01", "amount": "5"})
    assert r.status_code == 422 and "même profil" in r.json()["detail"]

    a, b = make.tx(mine["id"], "-1"), make.tx(hers["id"], "-1")
    r = client.post(f"{API}/transactions/categorize", json={"ids": [a["id"], b["id"]], "category_id": her_category["id"]})
    assert r.status_code == 422
    r = client.post(f"{API}/transactions/categorize", json={"ids": [b["id"]], "category_id": her_category["id"]})
    assert r.status_code == 200

    r = client.post(f"{API}/schedules", json={"account_id": mine["id"], "label": "X", "start_date": "2030-01-01", "amount": "-1", "category_id": her_category["id"]})
    assert r.status_code == 422
    r = client.post(f"{API}/schedules", json={"account_id": mine["id"], "label": "X", "start_date": "2030-01-01", "amount": "-1", "transfer_account_id": hers["id"]})
    assert r.status_code == 422

    budget = client.post(f"{API}/budgets", json={"account_id": mine["id"], "name": "B", "start_month": "2026-01-01"}).json()
    r = client.put(f"{API}/budgets/{budget['id']}/lines", json={"lines": [{"category_id": her_category["id"], "month": "2026-01", "amount": "10"}]})
    assert r.status_code == 404
    assert [b["id"] for b in client.get(f"{API}/budgets", params={"profile_id": other}).json()] == []

    r = client.post(f"{API}/payees/{her_payee['id']}/move", json={"target_id": tx["payee_id"] or 0})
    assert r.status_code in (404, 422)
    my_category = make.category("Loisirs")
    r = client.post(f"{API}/categories/{my_category['id']}/move", json={"target_id": her_category["id"]})
    assert r.status_code == 422


def test_profile_filter_on_schedules(client, make):
    other = _profile(client, "Bruno")["id"]
    mine, hers = make.account("Courant"), make.account("Livret", profile_id=other)
    for account in (mine, hers):
        r = client.post(f"{API}/schedules", json={"account_id": account["id"], "label": account["name"], "start_date": "2027-01-01", "amount": "-1"})
        assert r.status_code == 201, r.text
    assert [s["label"] for s in client.get(f"{API}/schedules", params={"profile_id": other}).json()] == ["Livret"]
    upcoming = client.get(f"{API}/schedules/upcoming", params={"profile_id": other, "days": 366}).json()
    assert upcoming and {o["account_id"] for o in upcoming} == {hers["id"]}


def test_migration_puts_existing_data_in_the_default_profile(tmp_path):
    """Base en 0007 avec des données : tout passe au profil 1, contraintes et liens conservés."""
    path = tmp_path / "old.sqlite"
    url = f"sqlite:///{path}"
    engine = create_engine(url)
    cfg = alembic_config(url)
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "0007")
    engine.dispose()
    db = sqlite3.connect(path)
    db.executescript(
        """
        INSERT INTO accounts (id, name, kind, currency, opening_balance, closed, position, is_default) VALUES (1, 'Courant', 'checking', 'EUR', 0, 0, 0, 1);
        INSERT INTO categories (id, name, kind, active, position) VALUES (1, 'Courses', 'expense', 1, 0);
        INSERT INTO classes (id, name, active, position) VALUES (1, 'Perso', 1, 0);
        INSERT INTO payees (id, name, active) VALUES (1, 'EDF', 1);
        INSERT INTO transactions (id, account_id, date, amount, payee_id, state) VALUES (1, 1, '2026-10-01', -500, 1, 0);
        INSERT INTO splits (id, transaction_id, category_id, class_id, amount, position) VALUES (1, 1, 1, 1, -500, 0);
        """
    )
    db.commit()
    db.close()

    upgrade(make_engine(url))
    db = sqlite3.connect(path)
    assert db.execute("SELECT id, name FROM profiles").fetchall() == [(1, "Default")]
    for table in ("accounts", "categories", "classes", "payees"):
        assert db.execute(f"SELECT DISTINCT profile_id FROM {table}").fetchall() == [(1,)], table
    assert db.execute("SELECT count(*) FROM transactions").fetchone() == (1,)
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    # Contraintes recréées : un nom par profil, un compte par défaut par profil, CHECK conservé.
    db.execute("INSERT INTO profiles (id, name, position) VALUES (2, 'Bruno', 1)")
    db.execute("INSERT INTO accounts (name, kind, currency, opening_balance, closed, position, is_default, profile_id) VALUES ('Courant', 'checking', 'EUR', 0, 0, 0, 1, 2)")
    for sql in (
        "INSERT INTO accounts (name, kind, currency, opening_balance, closed, position, is_default, profile_id) VALUES ('Courant', 'checking', 'EUR', 0, 0, 0, 0, 1)",
        "INSERT INTO accounts (name, kind, currency, opening_balance, closed, position, is_default, profile_id) VALUES ('Autre', 'checking', 'EUR', 0, 0, 0, 1, 1)",
        "INSERT INTO accounts (name, kind, currency, opening_balance, closed, position, is_default, profile_id) VALUES ('Bad', 'nope', 'EUR', 0, 0, 0, 0, 1)",
        "INSERT INTO payees (name, active, profile_id) VALUES ('EDF', 1, 1)",
    ):
        try:
            db.execute(sql)
        except sqlite3.IntegrityError:
            continue
        raise AssertionError(f"accepté : {sql}")
    db.close()


def test_delete_profile_with_all_its_data(client, make):
    other = _profile(client, "Bruno")["id"]
    keep = make.account("Courant")
    keep_tx = make.tx(keep["id"], "-7", payee_name="EDF")
    a = make.account("Courant", profile_id=other)
    b = make.account("Livret", profile_id=other)
    parent = make.category("Maison", profile_id=other)
    child = make.category("Travaux", parent_id=parent["id"])
    klass = client.post(f"{API}/classes", json={"name": "Perso", "profile_id": other}).json()
    tx = make.tx(a["id"], "-12", payee_name="EDF", category_id=child["id"], class_id=klass["id"])
    assert client.patch(f"{API}/transactions/{tx['id']}/state", json={"state": 2}).status_code == 200
    assert client.put(f"{API}/transactions/{tx['id']}/attachment", json={"kind": "link", "url": "https://example.org/x"}).status_code == 200
    r = client.post(f"{API}/transfers", json={"from_account_id": a["id"], "to_account_id": b["id"], "date": "2026-10-02", "amount": "50"})
    assert r.status_code == 201
    r = client.post(f"{API}/schedules", json={"account_id": a["id"], "label": "Loyer", "start_date": "2026-01-01", "amount": "-500", "transfer_account_id": b["id"]})
    assert r.status_code == 201
    budget = client.post(f"{API}/budgets", json={"account_id": a["id"], "name": "B", "start_month": "2026-01-01"}).json()
    r = client.put(f"{API}/budgets/{budget['id']}/lines", json={"lines": [{"category_id": parent["id"], "month": "2026-01", "amount": "10"}]})
    assert r.status_code == 200
    payee = client.get(f"{API}/payees", params={"profile_id": other}).json()[0]
    client.patch(f"{API}/payees/{payee['id']}", json={"default_category_id": child["id"]})

    listed = next(p for p in client.get(f"{API}/profiles").json() if p["id"] == other)
    assert listed["accounts"] == 2 and listed["transactions"] >= 3
    assert client.delete(f"{API}/profiles/{other}").status_code == 409  # sans with_data : refusé, rien de touché
    r = client.delete(f"{API}/profiles/{other}", params={"with_data": "true"})
    assert r.status_code == 204, r.text

    assert [p["id"] for p in client.get(f"{API}/profiles").json()] == [1]
    assert [x["id"] for x in client.get(f"{API}/accounts").json()] == [keep["id"]]
    assert client.get(f"{API}/transactions").json()["total"] == 1
    assert client.get(f"{API}/transactions/{keep_tx['id']}").json()["payee_id"] == keep_tx["payee_id"]
    assert client.get(f"{API}/categories").json() == [] and client.get(f"{API}/classes").json() == []
    assert [p["name"] for p in client.get(f"{API}/payees").json()] == ["EDF"]
    assert client.get(f"{API}/schedules").json() == [] and client.get(f"{API}/budgets").json() == []
    assert client.delete(f"{API}/profiles/1", params={"with_data": "true"}).status_code == 409  # jamais le dernier
