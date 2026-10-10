import pytest

API = "/api/v1"


def balances(client, account_id, **params):
    return client.get(f"{API}/accounts/{account_id}/balances", params=params).json()


def test_simple_transaction_gets_one_split(client, make):
    acc = make.account()
    cat = make.category("Alimentation")
    tx = make.tx(acc["id"], "-14,80", category_id=cat["id"], payment_method_id=1, label="Courses")
    assert tx["amount"] == "-14.80" and tx["state"] == 0
    assert tx["splits"] == [{"id": tx["splits"][0]["id"], "amount": "-14.80", "category_id": cat["id"], "class_id": None, "label": None}]


def test_split_must_match_amount(client, make):
    acc = make.account()
    a, b = make.category("A"), make.category("B")
    body = {"account_id": acc["id"], "date": "2026-10-01", "amount": "-30"}
    bad = client.post(f"{API}/transactions", json={**body, "splits": [{"amount": "-10", "category_id": a["id"]}]})
    assert bad.status_code == 422
    both = client.post(f"{API}/transactions", json={**body, "category_id": a["id"], "splits": [{"amount": "-30"}]})
    assert both.status_code == 422
    ok = client.post(
        f"{API}/transactions",
        json={**body, "splits": [{"amount": "-10", "category_id": a["id"]}, {"amount": "-20", "category_id": b["id"]}]},
    )
    assert ok.status_code == 201 and len(ok.json()["splits"]) == 2


def test_unknown_references(client, make):
    acc = make.account()
    body = {"account_id": acc["id"], "date": "2026-10-01", "amount": "-1"}
    assert client.post(f"{API}/transactions", json={**body, "account_id": 99}).status_code == 404
    assert client.post(f"{API}/transactions", json={**body, "category_id": 99}).status_code == 404
    assert client.post(f"{API}/transactions", json={**body, "payee_id": 99}).status_code == 404


def test_three_balances_ms_semantics(client, make):
    """Même logique que MS Comptes Bancaires : pointé inclut rapproché, total inclut le futur."""
    acc = make.account(opening_balance="100")
    make.tx(acc["id"], "-10", state=0)
    make.tx(acc["id"], "-20", state=1)
    make.tx(acc["id"], "-30", state=2)
    make.tx(acc["id"], "-5", date="2099-01-01")
    assert balances(client, acc["id"]) | {"account_id": None, "date": None} == {
        "account_id": None,
        "date": None,
        "total": "35.00",
        "pointed": "50.00",
        "reconciled": "70.00",
    }
    assert balances(client, acc["id"], date="2026-12-31")["total"] == "40.00"
    listed = client.get(f"{API}/accounts").json()[0]["balances"]
    assert listed == {"total": "35.00", "pointed": "50.00", "reconciled": "70.00"}


def test_state_transitions_and_bulk(client, make):
    acc = make.account()
    t1, t2 = make.tx(acc["id"], "-1"), make.tx(acc["id"], "-2")
    r = client.patch(f"{API}/transactions/{t1['id']}/state", json={"state": 2})
    assert r.json()["state"] == 2
    assert balances(client, acc["id"])["pointed"] == "-1.00"
    r = client.post(f"{API}/transactions/state", json={"ids": [t1["id"], t2["id"]], "state": 1})
    assert [t["state"] for t in r.json()] == [1, 1]
    assert client.post(f"{API}/transactions/state", json={"ids": [t1["id"], 999], "state": 1}).status_code == 404
    assert client.patch(f"{API}/transactions/{t1['id']}/state", json={"state": 3}).status_code == 422


def test_reconciled_transaction_is_locked(client, make):
    acc, other = make.account(), make.account("Livret A")
    cat = make.category("C")
    tx = make.tx(acc["id"], "-10", state=2, category_id=cat["id"])
    url = f"{API}/transactions/{tx['id']}"
    for change in ({"amount": "-11"}, {"date": "2026-01-01"}, {"account_id": other["id"]}, {"category_id": None}):
        assert client.patch(url, json=change).status_code == 409, change
    assert client.delete(url).status_code == 409
    assert client.patch(url, json={"label": "ok", "notes": "n", "amount": "-10"}).status_code == 200
    client.patch(f"{url}/state", json={"state": 0})
    assert client.patch(url, json={"amount": "-11"}).json()["splits"][0]["amount"] == "-11.00"
    assert client.delete(url).status_code == 204


def test_update_amount_of_split_transaction_requires_splits(client, make):
    acc = make.account()
    tx = make.tx(acc["id"], "-30", splits=[{"amount": "-10"}, {"amount": "-20"}])
    url = f"{API}/transactions/{tx['id']}"
    assert client.patch(url, json={"amount": "-40"}).status_code == 422
    r = client.patch(url, json={"amount": "-40", "splits": [{"amount": "-40"}]})
    assert r.status_code == 200 and len(r.json()["splits"]) == 1
    assert client.patch(url, json={"splits": None}).status_code == 422


def test_running_balance_over_period(client, make):
    acc = make.account(opening_balance="1000")
    make.tx(acc["id"], "-100", date="2026-08-15")
    make.tx(acc["id"], "-10", date="2026-09-01")
    make.tx(acc["id"], "50", date="2026-09-10", state=1)
    make.tx(acc["id"], "-1", date="2026-10-01")
    r = client.get(f"{API}/accounts/{acc['id']}/transactions", params={"start": "2026-09-01", "end": "2026-09-30"})
    body = r.json()
    assert body["opening_balance"] == "900.00"
    assert [(t["amount"], t["balance_after"]) for t in body["items"]] == [("-10.00", "890.00"), ("50.00", "940.00")]
    filtered = client.get(
        f"{API}/accounts/{acc['id']}/transactions", params={"start": "2026-09-01", "state": [1], "order": "desc"}
    ).json()["items"]
    assert [t["balance_after"] for t in filtered] == ["940.00"]


def test_search(client, make):
    acc = make.account()
    parent = make.category("Loisirs")
    child = make.category("Cinéma", parent_id=parent["id"])
    payee = client.post(f"{API}/payees", json={"name": "Pathé"}).json()
    make.tx(acc["id"], "-12", category_id=child["id"], payee_id=payee["id"], date="2026-09-01")
    make.tx(acc["id"], "-3", label="boulangerie", date="2026-09-02")
    by_cat = client.get(f"{API}/transactions", params={"category_id": parent["id"]}).json()
    assert by_cat["total"] == 1
    assert client.get(f"{API}/transactions", params={"category_id": parent["id"], "include_subcategories": False}).json()["total"] == 0
    assert client.get(f"{API}/transactions", params={"q": "path"}).json()["total"] == 1
    assert client.get(f"{API}/transactions", params={"q": "boul"}).json()["items"][0]["amount"] == "-3.00"
    page = client.get(f"{API}/transactions", params={"limit": 1, "order": "asc"}).json()
    assert page["total"] == 2 and page["items"][0]["amount"] == "-12.00"


class TestTransfers:
    @pytest.fixture
    def setup(self, client, make):
        cc, livret = make.account("Compte courant"), make.account("Livret A")
        epargne = make.category("Épargne", kind="savings")
        r = client.post(
            f"{API}/transfers",
            json={
                "from_account_id": cc["id"],
                "to_account_id": livret["id"],
                "date": "2026-10-01",
                "amount": "200",
                "category_id": epargne["id"],
                "from_state": 1,
            },
        )
        assert r.status_code == 201, r.text
        return cc, livret, r.json()

    def test_two_linked_legs(self, client, setup):
        cc, livret, t = setup
        src, dst = t["source"], t["destination"]
        assert (src["amount"], dst["amount"]) == ("-200.00", "200.00")
        assert (src["transfer_peer_id"], dst["transfer_peer_id"]) == (dst["id"], src["id"])
        assert (src["transfer_account_id"], dst["transfer_account_id"]) == (livret["id"], cc["id"])
        listed = client.get(f"{API}/accounts/{cc['id']}/transactions").json()["items"][0]
        assert listed["transfer_account_id"] == livret["id"]
        assert (src["state"], dst["state"]) == (1, 0)
        assert src["splits"][0]["category_id"] == dst["splits"][0]["category_id"]
        assert balances(client, cc["id"])["total"] == "-200.00"
        assert balances(client, livret["id"])["total"] == "200.00"

    def test_amount_and_date_follow(self, client, setup):
        _, _, t = setup
        client.patch(f"{API}/transactions/{t['source']['id']}", json={"amount": "-250", "date": "2026-10-02"})
        peer = client.get(f"{API}/transactions/{t['destination']['id']}").json()
        assert (peer["amount"], peer["date"], peer["splits"][0]["amount"]) == ("250.00", "2026-10-02", "250.00")

    def test_reconciled_peer_blocks_change(self, client, setup):
        _, _, t = setup
        client.patch(f"{API}/transactions/{t['destination']['id']}/state", json={"state": 2})
        assert client.patch(f"{API}/transactions/{t['source']['id']}", json={"amount": "-1"}).status_code == 409
        assert client.delete(f"{API}/transactions/{t['source']['id']}").status_code == 409

    def test_same_account_refused(self, client, setup):
        cc, livret, t = setup
        r = client.patch(f"{API}/transactions/{t['source']['id']}", json={"account_id": livret["id"]})
        assert r.status_code == 422
        r = client.post(
            f"{API}/transfers",
            json={"from_account_id": cc["id"], "to_account_id": cc["id"], "date": "2026-10-01", "amount": "1"},
        )
        assert r.status_code == 422

    def test_delete_removes_both(self, client, setup):
        _, _, t = setup
        assert client.delete(f"{API}/transactions/{t['source']['id']}").status_code == 204
        assert client.get(f"{API}/transactions/{t['destination']['id']}").status_code == 404

    def test_amount_must_be_positive(self, client, setup):
        cc, livret, _ = setup
        r = client.post(
            f"{API}/transfers",
            json={"from_account_id": cc["id"], "to_account_id": livret["id"], "date": "2026-10-01", "amount": "-5"},
        )
        assert r.status_code == 422
