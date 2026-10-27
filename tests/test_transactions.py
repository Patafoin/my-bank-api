import base64

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

    def test_cross_currency_refused(self, client, make):
        """No exchange-rate conversion anywhere in the app: both legs must share one currency."""
        eur = make.account("Compte EUR")
        usd = make.account("Compte USD", currency="usd")
        r = client.post(
            f"{API}/transfers",
            json={"from_account_id": eur["id"], "to_account_id": usd["id"], "date": "2026-10-01", "amount": "10"},
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


class TestAttachment:
    def test_link(self, client, make):
        acc = make.account()
        tx = make.tx(acc["id"], "-14.80")
        r = client.put(f"{API}/transactions/{tx['id']}/attachment", json={"kind": "link", "url": "https://example.org/facture.pdf"})
        assert r.status_code == 200
        body = r.json()
        assert body == {"kind": "link", "url": "https://example.org/facture.pdf", "filename": None, "mime_type": None, "size": None}
        assert client.get(f"{API}/transactions/{tx['id']}").json()["attachment"] == body

    def test_file(self, client, make):
        acc = make.account()
        tx = make.tx(acc["id"], "-14.80")
        content = base64.b64encode(b"%PDF-1.4 fake").decode()
        r = client.put(
            f"{API}/transactions/{tx['id']}/attachment",
            json={"kind": "file", "filename": "facture.pdf", "content_base64": content, "mime_type": "application/pdf"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body == {"kind": "file", "url": None, "filename": "facture.pdf", "mime_type": "application/pdf", "size": 13}
        got = client.get(f"{API}/transactions/{tx['id']}/attachment/content")
        assert got.status_code == 200 and got.content == b"%PDF-1.4 fake" and got.headers["content-type"] == "application/pdf"

    def test_replace_and_delete(self, client, make):
        acc = make.account()
        tx = make.tx(acc["id"], "-14.80")
        client.put(f"{API}/transactions/{tx['id']}/attachment", json={"kind": "link", "url": "https://a"})
        r = client.put(f"{API}/transactions/{tx['id']}/attachment", json={"kind": "link", "url": "https://b"})
        assert r.status_code == 200 and r.json()["url"] == "https://b"
        assert client.delete(f"{API}/transactions/{tx['id']}/attachment").status_code == 204
        assert client.get(f"{API}/transactions/{tx['id']}").json()["attachment"] is None
        # idempotent : retirer un justificatif déjà absent ne plante pas
        assert client.delete(f"{API}/transactions/{tx['id']}/attachment").status_code == 204

    def test_content_requires_a_file_attachment(self, client, make):
        acc = make.account()
        tx = make.tx(acc["id"], "-14.80")
        assert client.get(f"{API}/transactions/{tx['id']}/attachment/content").status_code == 404
        client.put(f"{API}/transactions/{tx['id']}/attachment", json={"kind": "link", "url": "https://a"})
        assert client.get(f"{API}/transactions/{tx['id']}/attachment/content").status_code == 404

    def test_link_must_be_http(self, client, make):
        acc = make.account()
        tx = make.tx(acc["id"], "-14.80")
        for url in ("javascript:alert(1)", "data:text/html,x", "JavaScript://x", "https:", "facture.pdf"):
            r = client.put(f"{API}/transactions/{tx['id']}/attachment", json={"kind": "link", "url": url})
            assert r.status_code == 422, url
        r = client.put(f"{API}/transactions/{tx['id']}/attachment", json={"kind": "link", "url": "HTTP://example.org/x"})
        assert r.status_code == 200

    def _put_file(self, client, tx, filename, mime_type):
        content = base64.b64encode(b"<script>alert(1)</script>").decode()
        body = {"kind": "file", "filename": filename, "content_base64": content, "mime_type": mime_type}
        assert client.put(f"{API}/transactions/{tx['id']}/attachment", json=body).status_code == 200
        return client.get(f"{API}/transactions/{tx['id']}/attachment/content")

    def test_active_content_is_downloaded_not_rendered(self, client, make):
        """HTML, SVG… servis tels quels exécuteraient du script sur l'origine de Home Assistant."""
        acc = make.account()
        tx = make.tx(acc["id"], "-14.80")
        for mime in ("text/html", "image/svg+xml", "application/xhtml+xml", None, "TEXT/HTML; charset=utf-8"):
            got = self._put_file(client, tx, "x.html", mime)
            assert got.headers["content-type"] == "application/octet-stream", mime
            assert got.headers["content-disposition"].startswith("attachment;")
            assert got.headers["x-content-type-options"] == "nosniff"
            assert got.headers["content-security-policy"].startswith("sandbox")
        got = self._put_file(client, tx, "photo.jpg", "image/jpeg")
        assert got.headers["content-type"] == "image/jpeg" and got.headers["content-disposition"].startswith("inline;")
        assert "content-security-policy" in got.headers
        got = self._put_file(client, tx, "facture.pdf", "application/pdf")
        assert got.headers["content-disposition"].startswith("inline;")
        assert "content-security-policy" not in got.headers  # la visionneuse PDF de Chrome refuse `sandbox`

    def test_filename_header(self, client, make):
        acc = make.account()
        tx = make.tx(acc["id"], "-14.80")
        got = self._put_file(client, tx, "reçu €.pdf", "application/pdf")
        assert got.status_code == 200
        assert got.headers["content-disposition"] == "inline; filename=\"re_u _.pdf\"; filename*=UTF-8''re%C3%A7u%20%E2%82%AC.pdf"
        got = self._put_file(client, tx, 'a"b\\c/d.pdf', "application/pdf")
        assert got.headers["content-disposition"] == "inline; filename=\"a_b_c/d.pdf\"; filename*=UTF-8''a%22b%5Cc%2Fd.pdf"

    def test_invalid_base64(self, client, make):
        acc = make.account()
        tx = make.tx(acc["id"], "-14.80")
        r = client.put(
            f"{API}/transactions/{tx['id']}/attachment",
            json={"kind": "file", "filename": "x.pdf", "content_base64": "not-base64!"},
        )
        assert r.status_code == 422

    def test_too_large(self, client, make):
        from my_bank_api.schemas import MAX_ATTACHMENT_SIZE

        acc = make.account()
        tx = make.tx(acc["id"], "-14.80")
        content = base64.b64encode(b"x" * (MAX_ATTACHMENT_SIZE + 1)).decode()
        r = client.put(
            f"{API}/transactions/{tx['id']}/attachment",
            json={"kind": "file", "filename": "x.bin", "content_base64": content},
        )
        assert r.status_code == 422

    def test_deleting_transaction_deletes_attachment(self, client, make):
        acc = make.account()
        tx = make.tx(acc["id"], "-14.80")
        client.put(f"{API}/transactions/{tx['id']}/attachment", json={"kind": "link", "url": "https://a"})
        assert client.delete(f"{API}/transactions/{tx['id']}").status_code == 204
