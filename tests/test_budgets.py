API = "/api/v1"


def _budget(client, **kw):
    r = client.post(f"{API}/budgets", json=kw)
    assert r.status_code == 201, r.text
    return r.json()


def _row(report, category_id):
    return next(r for r in report["rows"] if r["category_id"] == category_id)


def test_crud_months_and_overlap(client, make):
    acc = make.account()
    b = _budget(client, account_id=acc["id"], name="Budget 2026", start_month="2026-01")
    assert (b["start_month"], b["end_month"], b["months"]) == ("2026-01-01", "2026-12-01", 12)
    # Un seul budget par compte et par mois (H3).
    r = client.post(f"{API}/budgets", json={"account_id": acc["id"], "name": "Doublon", "start_month": "2026-06-15"})
    assert r.status_code == 409
    other = make.account("Livret A", kind="savings")
    assert client.post(f"{API}/budgets", json={"account_id": other["id"], "name": "Livret", "start_month": "2026-06-15"}).json()["start_month"] == "2026-06-01"
    assert client.patch(f"{API}/budgets/{b['id']}", json={"name": "Budget perso", "archived": True}).json()["archived"] is True
    assert [x["name"] for x in client.get(f"{API}/budgets", params={"account_id": acc["id"]}).json()] == ["Budget perso"]
    assert client.delete(f"{API}/accounts/{acc['id']}").status_code == 409
    assert client.delete(f"{API}/budgets/{b['id']}").status_code == 204


def test_grid_replace_and_shrink(client, make):
    acc = make.account()
    food = make.category("Alimentation")
    b = _budget(client, account_id=acc["id"], name="B", start_month="2026-01", months=3)
    lines = [
        {"category_id": food["id"], "month": "2026-01", "amount": "300"},
        {"category_id": food["id"], "month": "2026-02", "amount": "300"},
        {"category_id": food["id"], "month": "2026-03", "amount": "0"},
    ]
    out = client.put(f"{API}/budgets/{b['id']}/lines", json={"lines": lines}).json()
    assert out["months"] == ["2026-01", "2026-02", "2026-03"]
    assert [(x["month"], x["amount"]) for x in out["lines"]] == [("2026-01", "300.00"), ("2026-02", "300.00")]
    bad = client.put(f"{API}/budgets/{b['id']}/lines", json={"lines": [{"category_id": food["id"], "month": "2026-04", "amount": "1"}]})
    assert bad.status_code == 422
    assert client.put(f"{API}/budgets/{b['id']}/lines", json={"lines": lines[:1] * 2}).status_code == 422
    assert client.delete(f"{API}/categories/{food['id']}").status_code == 409
    client.patch(f"{API}/budgets/{b['id']}", json={"months": 1})
    assert [x["month"] for x in client.get(f"{API}/budgets/{b['id']}/lines").json()["lines"]] == ["2026-01"]


def test_copy_shifts_months(client, make):
    acc = make.account()
    food = make.category("Alimentation")
    b = _budget(client, account_id=acc["id"], name="2026", start_month="2026-01")
    client.put(f"{API}/budgets/{b['id']}/lines", json={"lines": [
        {"category_id": food["id"], "month": "2026-01", "amount": "310"},
        {"category_id": food["id"], "month": "2026-12", "amount": "500"},
    ]})
    r = client.post(f"{API}/budgets/{b['id']}/copy", json={"name": "2027"})
    assert r.status_code == 201, r.text
    copy = r.json()
    assert (copy["start_month"], copy["source_budget_id"]) == ("2027-01-01", b["id"])
    lines = client.get(f"{API}/budgets/{copy['id']}/lines").json()["lines"]
    assert [(x["month"], x["amount"]) for x in lines] == [("2027-01", "310.00"), ("2027-12", "500.00")]
    assert client.post(f"{API}/budgets/{b['id']}/copy", json={"name": "encore 2027"}).status_code == 409


def test_report_aggregates_subtrees_and_signs(client, make):
    cc = make.account()
    livret = make.account("Livret A", kind="savings")
    home = make.category("Logement")
    rent = make.category("Loyer", parent_id=home["id"])
    energy = make.category("Énergie", parent_id=home["id"])
    salary = make.category("Salaire", kind="income")
    savings = make.category("Épargne", kind="savings")

    b = _budget(client, account_id=cc["id"], name="2026", start_month="2026-01", months=2)
    client.put(f"{API}/budgets/{b['id']}/lines", json={"lines": [
        {"category_id": rent["id"], "month": "2026-01", "amount": "700"},
        {"category_id": energy["id"], "month": "2026-01", "amount": "80"},
        {"category_id": home["id"], "month": "2026-01", "amount": "20"},  # montant sur le parent (H2)
        {"category_id": salary["id"], "month": "2026-01", "amount": "2000"},
        {"category_id": savings["id"], "month": "2026-01", "amount": "100"},
    ]})
    make.tx(cc["id"], "-700", "2026-01-05", category_id=rent["id"])
    make.tx(cc["id"], "-95.50", "2026-01-20", category_id=energy["id"])
    make.tx(cc["id"], "-10", "2026-01-21", category_id=home["id"])  # opération sur le parent (H1)
    make.tx(cc["id"], "2100", "2026-01-28", category_id=salary["id"])
    make.tx(cc["id"], "-42", "2026-01-29")  # sans rubrique
    make.tx(cc["id"], "-999", "2026-03-01", category_id=rent["id"])  # hors période
    make.tx(livret["id"], "-50", "2026-01-10", category_id=rent["id"])  # autre compte
    client.post(f"{API}/transfers", json={"from_account_id": cc["id"], "to_account_id": livret["id"], "date": "2026-01-30",
                                          "amount": "150", "category_id": savings["id"]})

    report = client.get(f"{API}/budgets/{b['id']}/report").json()
    assert report["months"] == ["2026-01", "2026-02"]
    h = _row(report, home["id"])
    assert (h["has_children"], h["direction"]) == (True, -1)
    assert h["own_planned"][0] == "20.00" and h["planned"][0] == "800.00"
    assert h["actual"][0] == "805.50" and h["variance"][0] == "-5.50"  # dépassement = défavorable
    e = _row(report, energy["id"])
    assert (e["depth"], e["planned_total"], e["actual_total"], e["variance_total"]) == (1, "80.00", "95.50", "-15.50")
    s = _row(report, salary["id"])
    assert (s["direction"], s["actual"][0], s["variance"][0]) == (1, "2100.00", "100.00")
    sv = _row(report, savings["id"])
    assert (sv["direction"], sv["actual"][0], sv["variance"][0]) == (-1, "150.00", "50.00")
    assert report["uncategorized"] == ["-42.00", "0.00"]
    t = report["totals"]
    assert (t["expense"]["planned_total"], t["expense"]["actual_total"]) == ("800.00", "805.50")
    assert (t["income"]["actual_total"], t["savings"]["actual_total"]) == ("2100.00", "150.00")
    # net = effet signé sur le compte : 2100 - 805,50 - 150 - 42
    assert t["net"]["actual"][0] == "1102.50" and t["net"]["planned"][0] == "1100.00"

    # Sur le Livret A, l'épargne est une entrée (H6).
    lb = _budget(client, account_id=livret["id"], name="Livret 2026", start_month="2026-01", months=1)
    lr = client.get(f"{API}/budgets/{lb['id']}/report").json()
    assert (_row(lr, savings["id"])["direction"], _row(lr, savings["id"])["actual"][0]) == (1, "150.00")
