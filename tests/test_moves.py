API = "/api/v1"


def _move(client, kind, source, target, **kw):
    return client.post(f"{API}/{kind}/{source}/move", json={"target_id": target, **kw})


def test_move_category_includes_reconciled_and_schedules(client, make):
    acc = make.account()
    food = make.category("Alimentation")
    sub = make.category("Courses", parent_id=food["id"])
    other = make.category("Divers")
    t1 = make.tx(acc["id"], "-10", category_id=sub["id"])
    make.tx(acc["id"], "-5", category_id=sub["id"])
    split = make.tx(acc["id"], "-30", splits=[{"amount": "-20", "category_id": sub["id"]}, {"amount": "-10", "category_id": sub["id"]}])
    client.patch(f"{API}/transactions/{t1['id']}/state", json={"state": 2})  # rapprochée : déplacée quand même
    client.post(f"{API}/schedules", json={"account_id": acc["id"], "label": "Panier", "start_date": "2030-01-01", "amount": "-5", "category_id": sub["id"]})
    before = client.get(f"{API}/accounts/{acc['id']}/balances").json()
    r = _move(client, "categories", sub["id"], other["id"])
    assert r.status_code == 200, r.text
    assert r.json() == {"transactions": 3, "schedules": 1}
    counts = {c["name"]: c["transaction_count"] for c in client.get(f"{API}/categories").json()}
    assert counts["Courses"] == 0 and counts["Divers"] == 3
    assert {s["category_id"] for s in client.get(f"{API}/transactions/{split['id']}").json()["splits"]} == {other["id"]}
    assert client.get(f"{API}/accounts/{acc['id']}/balances").json() == before
    # vide : la source peut maintenant être supprimée
    assert client.delete(f"{API}/categories/{sub['id']}").status_code == 204
    assert _move(client, "categories", other["id"], other["id"]).status_code == 422
    assert _move(client, "categories", other["id"], 9999).status_code == 404


def test_move_class_and_payee(client, make):
    acc = make.account()
    c1 = client.post(f"{API}/classes", json={"name": "Perso"}).json()
    c2 = client.post(f"{API}/classes", json={"name": "Famille"}).json()
    p1 = client.post(f"{API}/payees", json={"name": "Primeru"}).json()
    p2 = client.post(f"{API}/payees", json={"name": "Primeur"}).json()
    for _ in range(3):
        make.tx(acc["id"], "-1", class_id=c1["id"], payee_id=p1["id"])
    assert _move(client, "classes", c1["id"], c2["id"]).json() == {"transactions": 3, "schedules": 0}
    assert _move(client, "payees", p1["id"], p2["id"]).json() == {"transactions": 3, "schedules": 0}
    payees = {p["name"]: p["transaction_count"] for p in client.get(f"{API}/payees").json()}
    assert payees == {"Primeur": 3, "Primeru": 0}
    assert client.delete(f"{API}/payees/{p1['id']}").status_code == 204


def test_move_account_rules(client, make):
    old = make.account("Ancienne carte", kind="card")
    cc = make.account("Compte courant")
    livret = make.account("Livret A", kind="savings")
    make.tx(old["id"], "-10")
    locked = make.tx(old["id"], "-20")
    client.patch(f"{API}/transactions/{locked['id']}/state", json={"state": 2})
    tr = client.post(f"{API}/transfers", json={"from_account_id": old["id"], "to_account_id": livret["id"], "amount": "5", "date": "2026-10-02"})
    assert tr.status_code == 201
    # rapprochées : refus sans l'option
    r = _move(client, "accounts", old["id"], cc["id"])
    assert r.status_code == 409 and "1 opération(s) rapprochée(s)" in r.json()["detail"]
    # virements entre source et cible : refus
    r = _move(client, "accounts", old["id"], livret["id"], include_reconciled=True)
    assert r.status_code == 409 and "vers lui-même" in r.json()["detail"]
    r = _move(client, "accounts", old["id"], cc["id"], include_reconciled=True)
    assert r.json() == {"transactions": 3, "schedules": 0}
    accounts = {a["name"]: a for a in client.get(f"{API}/accounts").json()}
    assert accounts["Compte courant"]["balances"] == {"total": "-35.00", "pointed": "-20.00", "reconciled": "-20.00"}
    assert accounts["Ancienne carte"]["transaction_count"] == 0
    assert client.delete(f"{API}/accounts/{old['id']}").status_code == 204
    # planification de virement entre les deux : refus
    a = make.account("A")
    b = make.account("B")
    client.post(f"{API}/schedules", json={"account_id": a["id"], "label": "Épargne", "start_date": "2030-01-01", "amount": "-50", "transfer_account_id": b["id"]})
    r = _move(client, "accounts", b["id"], a["id"])
    assert r.status_code == 409 and "planifié" in r.json()["detail"]
    # une planification d'un autre compte vers la source suit le déplacement
    client.post(f"{API}/schedules", json={"account_id": cc["id"], "label": "Vers B", "start_date": "2030-01-01", "amount": "-5", "transfer_account_id": b["id"]})
    c = make.account("C")
    assert _move(client, "accounts", b["id"], c["id"]).json() == {"transactions": 0, "schedules": 2}
    assert {s["transfer_account_id"] for s in client.get(f"{API}/schedules").json()} == {c["id"]}
