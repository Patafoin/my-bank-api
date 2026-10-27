def test_account_crud(client, make):
    acc = make.account("Compte courant", opening_balance="100,50", kind="checking")
    assert acc["opening_balance"] == "100.50"
    assert acc["balances"] == {"total": "100.50", "pointed": "100.50", "reconciled": "100.50"}
    assert client.post("/api/v1/accounts", json={"name": "Compte courant"}).status_code == 409
    r = client.patch(f"/api/v1/accounts/{acc['id']}", json={"name": "CC", "closed": True})
    assert r.status_code == 200 and r.json()["name"] == "CC" and r.json()["closed"] is True
    assert client.patch(f"/api/v1/accounts/{acc['id']}", json={"name": None}).status_code == 422
    r = client.patch(f"/api/v1/accounts/{acc['id']}", json={"opening_balance": "0,00", "opening_date": None, "notes": "n"})
    assert r.status_code == 200, r.text
    assert r.json()["opening_balance"] == "0.00" and r.json()["balances"]["total"] == "0.00"
    assert client.delete(f"/api/v1/accounts/{acc['id']}").status_code == 204
    assert client.get(f"/api/v1/accounts/{acc['id']}").status_code == 404


def test_account_currency(client, make):
    acc = make.account("Compte courant")
    assert acc["currency"] == "EUR"
    usd = make.account("Compte US", currency="usd")
    assert usd["currency"] == "USD"
    r = client.patch(f"/api/v1/accounts/{acc['id']}", json={"currency": "gbp"})
    assert r.status_code == 200 and r.json()["currency"] == "GBP"
    assert client.patch(f"/api/v1/accounts/{acc['id']}", json={"currency": None}).status_code == 422
    assert client.post("/api/v1/accounts", json={"name": "Invalide", "currency": "E"}).status_code == 422
    assert client.post("/api/v1/accounts", json={"name": "Invalide2", "currency": "EURO"}).status_code == 422


def test_account_default_is_exclusive(client, make):
    a = make.account("A", is_default=True)
    b = make.account("B")
    assert a["is_default"] is True and b["is_default"] is False
    r = client.patch(f"/api/v1/accounts/{b['id']}", json={"is_default": True})
    assert r.status_code == 200 and r.json()["is_default"] is True
    # le précédent compte par défaut a été désactivé automatiquement
    assert client.get(f"/api/v1/accounts/{a['id']}").json()["is_default"] is False
    # on peut retirer le compte par défaut sans qu'un autre le devienne
    assert client.patch(f"/api/v1/accounts/{b['id']}", json={"is_default": False}).json()["is_default"] is False
    assert all(not acc["is_default"] for acc in client.get("/api/v1/accounts").json())


def test_account_with_transactions_cannot_be_deleted(client, make):
    acc = make.account()
    make.tx(acc["id"], "-1")
    assert client.delete(f"/api/v1/accounts/{acc['id']}").status_code == 409


def test_category_tree_kind_and_path(client, make):
    loisirs = make.category("Loisirs")
    sorties = make.category("Sorties", parent_id=loisirs["id"])
    resto = make.category("Restaurant", parent_id=sorties["id"])
    assert resto["kind"] == "expense"
    assert resto["path"] == "Loisirs > Sorties > Restaurant" and resto["depth"] == 2
    # une racine sans type est refusée, un enfant au type contraire aussi
    assert client.post("/api/v1/categories", json={"name": "X"}).status_code == 422
    r = client.post("/api/v1/categories", json={"name": "Y", "parent_id": loisirs["id"], "kind": "income"})
    assert r.status_code == 422
    # doublon de nom au même niveau
    assert client.post("/api/v1/categories", json={"name": "sorties", "parent_id": loisirs["id"]}).status_code == 409
    # ordre de parcours
    paths = [c["path"] for c in client.get("/api/v1/categories").json()]
    assert paths == ["Loisirs", "Loisirs > Sorties", "Loisirs > Sorties > Restaurant"]


def test_tree_transaction_count_is_direct_only(client, make):
    acc = make.account()
    parent = make.category("Loisirs")
    child = make.category("Sorties", parent_id=parent["id"])
    other = make.category("Divers")
    cls = client.post("/api/v1/classes", json={"name": "Vacances"}).json()
    make.tx(acc["id"], "-10", category_id=parent["id"], class_id=cls["id"])
    make.tx(acc["id"], "-20", category_id=child["id"])
    make.tx(acc["id"], "-30", category_id=child["id"])
    # deux lignes sur la même rubrique : une seule opération
    make.tx(acc["id"], "-30", splits=[{"amount": "-10", "category_id": child["id"]}, {"amount": "-20", "category_id": child["id"]}])
    make.tx(acc["id"], "-30", splits=[{"amount": "-10", "category_id": child["id"]}, {"amount": "-20", "category_id": other["id"]}])
    counts = {c["name"]: c["transaction_count"] for c in client.get("/api/v1/categories").json()}
    assert counts == {"Loisirs": 1, "Sorties": 4, "Divers": 1}
    assert client.get(f"/api/v1/categories/{child['id']}").json()["transaction_count"] == 4
    assert make.category("Vide")["transaction_count"] == 0
    assert client.get("/api/v1/classes").json()[0]["transaction_count"] == 1


def test_category_cycle_refused(client, make):
    a = make.category("A")
    b = make.category("B", parent_id=a["id"])
    assert client.patch(f"/api/v1/categories/{a['id']}", json={"parent_id": b["id"]}).status_code == 422
    assert client.patch(f"/api/v1/categories/{a['id']}", json={"parent_id": a["id"]}).status_code == 422


def test_category_move_keeps_kind_and_root_kind_change_propagates(client, make):
    depenses = make.category("Dépenses", kind="expense")
    loisirs = make.category("Loisirs", kind="expense")
    epargne = make.category("Épargne", kind="savings")
    sub = make.category("Divers", parent_id=depenses["id"])
    leaf = make.category("Feuille", parent_id=sub["id"])
    # déplacement sous un parent d'un autre type : refusé, rien ne bouge
    r = client.patch(f"/api/v1/categories/{sub['id']}", json={"parent_id": epargne["id"]})
    assert r.status_code == 422 and "épargne" in r.json()["detail"]
    r = client.patch(f"/api/v1/categories/{sub['id']}", json={"parent_id": epargne["id"], "kind": "savings"})
    assert r.status_code == 422
    assert client.get(f"/api/v1/categories/{sub['id']}").json()["parent_id"] == depenses["id"]
    # une racine non plus ne peut pas aller sous un parent d'un autre type
    assert client.patch(f"/api/v1/categories/{epargne['id']}", json={"parent_id": depenses["id"]}).status_code == 422
    # déplacement sous un parent du même type : accepté
    r = client.patch(f"/api/v1/categories/{sub['id']}", json={"parent_id": loisirs["id"]})
    assert r.status_code == 200 and r.json()["kind"] == "expense"
    # devenir racine garde le type
    assert client.patch(f"/api/v1/categories/{sub['id']}", json={"parent_id": None}).json()["kind"] == "expense"
    # changer le type d'une racine le propage à tout son sous-arbre
    client.patch(f"/api/v1/categories/{sub['id']}", json={"kind": "income"})
    assert client.get(f"/api/v1/categories/{leaf['id']}").json()["kind"] == "income"
    assert client.patch(f"/api/v1/categories/{leaf['id']}", json={"kind": "expense"}).status_code == 422


def test_category_delete_rules(client, make):
    parent = make.category("P")
    child = make.category("C", parent_id=parent["id"])
    assert client.delete(f"/api/v1/categories/{parent['id']}").status_code == 409
    acc = make.account()
    make.tx(acc["id"], "-5", category_id=child["id"])
    assert client.delete(f"/api/v1/categories/{child['id']}").status_code == 409


def test_classes_tree(client):
    r = client.post("/api/v1/classes", json={"name": "Personnes"})
    parent = r.json()
    r = client.post("/api/v1/classes", json={"name": "Bob", "parent_id": parent["id"]})
    assert r.status_code == 201 and r.json()["path"] == "Personnes > Bob"


def test_payee_full_record_and_search(client, make):
    cat = make.category("Alimentation")
    body = {
        "name": "Primeur",
        "short_name": "LCL",
        "default_category_id": cat["id"],
        "city": "Rennes",
        "iban": "FR7612345",
        "swift": "AGRIFRPP",
        "free1": "x",
        "external_code": "42",
    }
    r = client.post("/api/v1/payees", json=body)
    assert r.status_code == 201, r.text
    assert r.json()["default_category_id"] == cat["id"] and r.json()["iban"] == "FR7612345"
    assert client.post("/api/v1/payees", json={"name": "Primeur"}).status_code == 409
    assert client.post("/api/v1/payees", json={"name": "Z", "default_category_id": 999}).status_code == 404
    assert [p["name"] for p in client.get("/api/v1/payees", params={"q": "prim"}).json()] == ["Primeur"]
    assert client.post("/api/v1/payees", json={"name": "Z", "unknown": 1}).status_code == 422


def test_payment_method_in_use_cannot_be_deleted(client, make):
    acc = make.account()
    make.tx(acc["id"], "-1", payment_method_id=1)
    assert client.delete("/api/v1/payment-methods/1").status_code == 409
    assert client.delete("/api/v1/payment-methods/2").status_code == 204


def test_account_and_payee_transaction_count(client, make):
    cc = make.account()
    livret = make.account("Livret A", kind="savings")
    payee = client.post("/api/v1/payees", json={"name": "Primeur"}).json()
    assert payee["transaction_count"] == 0
    make.tx(cc["id"], "-10", payee_id=payee["id"])
    make.tx(cc["id"], "-5", payee_id=payee["id"])
    make.tx(livret["id"], "20")
    r = client.post("/api/v1/transfers", json={"from_account_id": cc["id"], "to_account_id": livret["id"], "amount": "50", "date": "2026-10-02"})
    assert r.status_code == 201, r.text
    counts = {a["name"]: a["transaction_count"] for a in client.get("/api/v1/accounts").json()}
    assert counts == {"Compte courant": 3, "Livret A": 2}  # chaque jambe du virement compte sur son compte
    assert client.get(f"/api/v1/accounts/{cc['id']}").json()["transaction_count"] == 3
    assert client.patch(f"/api/v1/accounts/{cc['id']}", json={"notes": "x"}).json()["transaction_count"] == 3
    listed = {p["name"]: p["transaction_count"] for p in client.get("/api/v1/payees").json()}
    assert listed["Primeur"] == 2
    assert client.get(f"/api/v1/payees/{payee['id']}").json()["transaction_count"] == 2
    assert client.patch(f"/api/v1/payees/{payee['id']}", json={"city": "Rennes"}).json()["transaction_count"] == 2

