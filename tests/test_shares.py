from my_bank_api import shares
from my_bank_api.shares import Person

API = "/api/v1/shared-accounts"


def _people(*paid):
    return [Person(i + 1, f"P{i + 1}", cents) for i, cents in enumerate(paid)]


def test_shares_split_the_remainder_cents():
    # 100,00 € entre trois : 33,34 pour le plus gros payeur, 33,33 pour les autres ; la somme fait 100,00.
    s = shares.shares(_people(10000, 0, 0))
    assert s == {1: 3334, 2: 3333, 3: 3333}
    assert sum(s.values()) == 10000


def test_next_payer_is_who_paid_least_first_in_order_on_tie():
    assert shares.next_payer(_people(5000, 2000, 3000)) == 2
    assert shares.next_payer(_people(0, 0, 0)) == 1
    assert shares.next_payer(_people(1000, 0, 0)) == 2


def test_settlement_balances_everyone_with_at_most_n_minus_1_transfers():
    people = _people(30000, 6000, 0, 4000)  # total 400 € → 100 € chacun
    transfers = shares.settlement(people)
    assert [(t.from_id, t.to_id, t.amount) for t in transfers] == [(3, 1, 10000), (4, 1, 6000), (2, 1, 4000)]
    paid = {p.id: p.paid for p in people}
    for t in transfers:
        paid[t.from_id] += t.amount
        paid[t.to_id] -= t.amount
    assert set(paid.values()) == {10000}
    assert shares.settlement(_people(5000, 5000)) == []


def test_holiday_scenario(client):
    """Trois personnes en vacances : chacun paie à tour de rôle, puis on clôture."""
    r = client.post(API, json={"name": "Vacances d'été", "participants": ["Alice", "Bruno", "Chloé"]})
    assert r.status_code == 201, r.text
    acc = r.json()
    alice, bruno, chloe = (p["id"] for p in acc["participants"])
    assert acc["next_participant_id"] == alice and acc["total"] == "0.00" and acc["settlement"] == []

    def spend(who, amount, **kw):
        r = client.post(f"{API}/{acc['id']}/expenses", json={"participant_id": who, "amount": amount, **kw})
        assert r.status_code == 201, r.text
        return r.json()

    out = spend(alice, "120,50", label="Courses", date="2026-07-01")
    assert out["next_participant_id"] == bruno
    out = spend(bruno, "80")
    assert out["next_participant_id"] == chloe
    assert out["expenses"][0]["date"] == out["created_on"]  # date du jour par défaut, plus récente en tête
    out = spend(chloe, "45.30", label="Glaces")
    assert out["next_participant_id"] == chloe  # toujours celle qui a le moins payé
    assert out["total"] == "245.80"
    by_id = {p["id"]: p for p in out["participants"]}
    assert [by_id[i]["share"] for i in (alice, bruno, chloe)] == ["81.94", "81.93", "81.93"]
    assert by_id[alice]["balance"] == "38.56" and by_id[chloe]["balance"] == "-36.63"
    # Équilibrage provisoire : Chloé rembourse Alice, Bruno aussi.
    assert [(t["from_name"], t["to_name"], t["amount"]) for t in out["settlement"]] == [("Chloé", "Alice", "36.63"), ("Bruno", "Alice", "1.93")]

    # Modification et suppression d'une dépense
    glaces = next(e for e in out["expenses"] if e["label"] == "Glaces")
    out = client.patch(f"{API}/{acc['id']}/expenses/{glaces['id']}", json={"amount": "50", "participant_id": bruno}).json()
    assert {p["name"]: p["paid"] for p in out["participants"]} == {"Alice": "120.50", "Bruno": "130.00", "Chloé": "0.00"}
    out = client.delete(f"{API}/{acc['id']}/expenses/{glaces['id']}").json()
    assert out["total"] == "200.50"

    # Clôture : figé, remboursements définitifs
    out = client.patch(f"{API}/{acc['id']}", json={"closed": True}).json()
    assert out["closed"] and out["closed_on"] and out["next_participant_id"] is None
    assert [(t["from_name"], t["to_name"], t["amount"]) for t in out["settlement"]] == [("Chloé", "Alice", "53.66"), ("Chloé", "Bruno", "13.17")]
    assert client.post(f"{API}/{acc['id']}/expenses", json={"participant_id": bruno, "amount": "1"}).status_code == 409
    assert client.post(f"{API}/{acc['id']}/participants", json={"name": "Léa"}).status_code == 409
    assert client.patch(f"{API}/{acc['id']}", json={"name": "Bretagne 2026"}).json()["name"] == "Bretagne 2026"
    listed = client.get(API).json()
    assert [a["name"] for a in listed] == ["Bretagne 2026"] and "expenses" not in listed[0]
    # Réouverture
    assert client.patch(f"{API}/{acc['id']}", json={"closed": False}).json()["closed"] is False
    assert client.delete(f"{API}/{acc['id']}").status_code == 204
    assert client.get(f"{API}/{acc['id']}").status_code == 404


def test_settlement_after_close_equalises_spending(client):
    acc = client.post(API, json={"name": "Week-end", "participants": ["A", "B", "C"]}).json()
    a, b, c = (p["id"] for p in acc["participants"])
    for who, amount in ((a, "100"), (b, "40"), (c, "10.01")):
        client.post(f"{API}/{acc['id']}/expenses", json={"participant_id": who, "amount": amount})
    out = client.patch(f"{API}/{acc['id']}", json={"closed": True}).json()
    paid = {p["id"]: float(p["paid"]) for p in out["participants"]}
    for t in out["settlement"]:
        paid[t["from_participant_id"]] += float(t["amount"])
        paid[t["to_participant_id"]] -= float(t["amount"])
    shares_ = {p["id"]: float(p["share"]) for p in out["participants"]}
    assert all(abs(paid[i] - shares_[i]) < 0.001 for i in paid)
    assert max(shares_.values()) - min(shares_.values()) <= 0.01
    assert len(out["settlement"]) <= 2


def test_participants_rules(client):
    assert client.post(API, json={"name": "Seul", "participants": ["Moi"]}).status_code == 422
    assert client.post(API, json={"name": "Doublon", "participants": ["Bruno", "bruno"]}).status_code == 422
    acc = client.post(API, json={"name": "Ski", "participants": ["Alice", "Bruno"]}).json()
    alice, bruno = (p["id"] for p in acc["participants"])
    out = client.post(f"{API}/{acc['id']}/participants", json={"name": "Chloé"})
    assert out.status_code == 201 and [p["name"] for p in out.json()["participants"]] == ["Alice", "Bruno", "Chloé"]
    chloe = out.json()["participants"][2]["id"]
    assert client.post(f"{API}/{acc['id']}/participants", json={"name": "CHLOÉ"}).status_code == 409
    assert client.patch(f"{API}/{acc['id']}/participants/{chloe}", json={"name": "Chloé B."}).json()["participants"][2]["name"] == "Chloé B."
    client.post(f"{API}/{acc['id']}/expenses", json={"participant_id": bruno, "amount": "10"})
    r = client.delete(f"{API}/{acc['id']}/participants/{bruno}")
    assert r.status_code == 409 and "déjà payé" in r.json()["detail"]
    assert client.delete(f"{API}/{acc['id']}/participants/{chloe}").status_code == 200
    assert client.delete(f"{API}/{acc['id']}/participants/{alice}").status_code == 409  # il en faut deux
    assert client.post(f"{API}/{acc['id']}/expenses", json={"participant_id": bruno, "amount": "-5"}).status_code == 422
    assert client.post(f"{API}/{acc['id']}/expenses", json={"participant_id": 9999, "amount": "5"}).status_code == 404
    # supprimer le compte supprime participants et dépenses
    assert client.delete(f"{API}/{acc['id']}").status_code == 204
