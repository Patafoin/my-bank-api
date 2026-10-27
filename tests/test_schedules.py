import datetime as dt

import pytest

from my_bank_api import schedules as sched
from my_bank_api.models import Schedule

API = "/api/v1"


@pytest.fixture
def today(monkeypatch):
    """Jour courant réglable : today.set(date)."""

    class Today:
        value = dt.date(2026, 10, 7)

        def set(self, value):
            self.value = value

    t = Today()
    monkeypatch.setattr(sched, "today", lambda: t.value)
    return t


def _schedule(client, **kw):
    r = client.post(f"{API}/schedules", json=kw)
    assert r.status_code == 201, r.text
    return r.json()


def _txs(client, account_id):
    return client.get(f"{API}/accounts/{account_id}/transactions").json()["items"]


# --- Dates -------------------------------------------------------------------------------------


def _dates(start, unit, every, n):
    return [sched.occurrence_date(start, unit, every, r) for r in range(1, n + 1)]


def test_month_end_is_clamped_then_restored():
    assert _dates(dt.date(2026, 1, 31), "month", 1, 4) == [
        dt.date(2026, 1, 31),
        dt.date(2026, 2, 28),
        dt.date(2026, 3, 31),
        dt.date(2026, 4, 30),
    ]


def test_february_29_yearly():
    assert _dates(dt.date(2024, 2, 29), "year", 1, 5) == [
        dt.date(2024, 2, 29),
        dt.date(2025, 2, 28),
        dt.date(2026, 2, 28),
        dt.date(2027, 2, 28),
        dt.date(2028, 2, 29),
    ]


def test_quarters_weeks_days_and_every():
    assert _dates(dt.date(2026, 11, 30), "quarter", 1, 3) == [dt.date(2026, 11, 30), dt.date(2027, 2, 28), dt.date(2027, 5, 30)]
    assert _dates(dt.date(2026, 1, 1), "week", 2, 3) == [dt.date(2026, 1, 1), dt.date(2026, 1, 15), dt.date(2026, 1, 29)]
    assert _dates(dt.date(2026, 1, 30), "day", 3, 2) == [dt.date(2026, 1, 30), dt.date(2026, 2, 2)]
    assert _dates(dt.date(2026, 1, 15), "month", 2, 3) == [dt.date(2026, 1, 15), dt.date(2026, 3, 15), dt.date(2026, 5, 15)]


def test_end_date_gives_count():
    s = Schedule(start_date=dt.date(2026, 1, 31), unit="month", every=1, end_mode="date", end_date=dt.date(2026, 6, 29))
    assert sched.last_rank(s) == 5  # 31/01, 28/02, 31/03, 30/04, 31/05 (30/06 dépasse)


# --- Génération --------------------------------------------------------------------------------


def test_create_generates_what_is_due_and_nothing_twice(client, make, today):
    acc = make.account()
    cat = make.category("Abonnements")
    s = _schedule(
        client, account_id=acc["id"], label="Box internet", amount="-29,99", start_date="2026-10-05",
        category_id=cat["id"], payee_name="Free", generate_from="2026-10-01",
    )
    txs = _txs(client, acc["id"])
    assert [(t["date"], t["amount"], t["label"]) for t in txs] == [("2026-10-05", "-29.99", "Box internet")]
    assert txs[0]["splits"][0]["category_id"] == cat["id"] and txs[0]["payee_id"] == s["payee_id"]
    assert s["generated"] == 1 and s["next_date"] == "2026-11-05" and s["remaining"] is None

    # Relancer la génération (redémarrage, tâche de nuit) ne crée rien de plus.
    assert client.post(f"{API}/schedules/generate").json()["created"] == []
    today.set(dt.date(2027, 1, 10))
    created = client.post(f"{API}/schedules/generate").json()["created"]
    assert [t["date"] for t in created] == ["2026-11-05", "2026-12-05", "2027-01-05"]
    assert client.post(f"{API}/schedules/generate").json()["created"] == []
    assert len(_txs(client, acc["id"])) == 4


def test_generate_from_defaults_to_today_so_past_is_not_replayed(client, make, today):
    acc = make.account()
    s = _schedule(client, account_id=acc["id"], label="Prêt", amount="-300", start_date="2025-01-10", mode="installments", end_mode="count", count=24)
    assert _txs(client, acc["id"]) == []
    assert s["generate_from"] == "2026-10-07" and s["next_date"] == "2026-10-10" and s["remaining"] == 3
    items = client.get(f"{API}/schedules/{s['id']}/occurrences").json()["items"]
    assert len(items) == 24
    assert {o["status"] for o in items[:21]} == {"ignored"} and {o["status"] for o in items[21:]} == {"pending"}


def test_lead_days_and_manual_mode(client, make, today):
    acc = make.account()
    early = _schedule(client, account_id=acc["id"], label="Loyer", amount="-700", start_date="2026-10-10", lead_days=5)
    assert early["generated"] == 1  # 10/10 ≤ 07/10 + 5 jours
    manual = _schedule(client, account_id=acc["id"], label="Impôts", amount="-120", start_date="2026-10-01", auto=False, generate_from="2026-10-01")
    assert manual["generated"] == 0
    up = client.get(f"{API}/schedules/upcoming", params={"days": 30}).json()
    assert [(o["label"], o["date"], o["overdue"]) for o in up] == [("Impôts", "2026-10-01", True), ("Impôts", "2026-11-01", False)]
    r = client.post(f"{API}/schedules/{manual['id']}/occurrences/1/generate")
    assert r.status_code == 201 and r.json()["created"][0]["amount"] == "-120.00"
    assert client.post(f"{API}/schedules/{manual['id']}/occurrences/1/generate").status_code == 409


def test_skip_and_restore(client, make, today):
    acc = make.account()
    s = _schedule(client, account_id=acc["id"], label="Assurance", amount="-50", start_date="2026-11-01")
    occ = client.post(f"{API}/schedules/{s['id']}/occurrences/1/skip").json()["items"]
    assert occ[0]["status"] == "skipped"
    today.set(dt.date(2026, 12, 2))
    assert [t["date"] for t in client.post(f"{API}/schedules/generate").json()["created"]] == ["2026-12-01"]
    # Rétablie alors qu'elle est passée : générée tout de suite.
    occ = client.delete(f"{API}/schedules/{s['id']}/occurrences/1/skip").json()["items"]
    assert occ[0]["status"] == "generated"
    assert sorted(t["date"] for t in _txs(client, acc["id"])) == ["2026-11-01", "2026-12-01"]


def test_transfer_schedule_creates_twin_legs(client, make, today):
    cc = make.account()
    livret = make.account("Livret A", kind="savings")
    epargne = make.category("Épargne", kind="savings")
    _schedule(
        client, account_id=cc["id"], transfer_account_id=livret["id"], label="Épargne mensuelle",
        amount="-100", start_date="2026-10-07", category_id=epargne["id"],
    )
    out, inc = _txs(client, cc["id"]), _txs(client, livret["id"])
    assert out[0]["amount"] == "-100.00" and inc[0]["amount"] == "100.00"
    assert out[0]["transfer_peer_id"] == inc[0]["id"] and inc[0]["splits"][0]["category_id"] == epargne["id"]


def test_transfer_schedule_cross_currency_refused(client, make):
    cc = make.account()
    usd = make.account("Compte USD", currency="usd")
    r = client.post(
        f"{API}/schedules",
        json={"account_id": cc["id"], "transfer_account_id": usd["id"], "label": "x", "amount": "-100", "start_date": "2026-10-07"},
    )
    assert r.status_code == 422


# --- Échéancier (grille) -----------------------------------------------------------------------


def test_installment_grid_keeps_own_amounts_when_rule_changes(client, make, today):
    acc = make.account()
    s = _schedule(client, account_id=acc["id"], label="Travaux", amount="-100", start_date="2026-11-15",
                  mode="installments", end_mode="count", count=4)
    grid = [{"rank": 1, "amount": "-100"}, {"rank": 2, "amount": "-150"}, {"rank": 3, "amount": "-150"}, {"rank": 4, "amount": "-80,5"}]
    occ = client.put(f"{API}/schedules/{s['id']}/occurrences", json={"items": grid}).json()["items"]
    assert [(o["amount"], o["own_amount"]) for o in occ] == [("-100.00", False), ("-150.00", True), ("-150.00", True), ("-80.50", True)]

    # Décaler la première date : les dates suivent, les montants propres restent attachés au rang.
    client.patch(f"{API}/schedules/{s['id']}", json={"start_date": "2026-12-01"})
    occ = client.get(f"{API}/schedules/{s['id']}/occurrences").json()["items"]
    assert [(o["date"], o["amount"]) for o in occ] == [
        ("2026-12-01", "-100.00"), ("2027-01-01", "-150.00"), ("2027-02-01", "-150.00"), ("2027-03-01", "-80.50"),
    ]
    # Raccourcir : le rang 4 et son montant disparaissent.
    s2 = client.patch(f"{API}/schedules/{s['id']}", json={"count": 3}).json()
    assert s2["total_occurrences"] == 3 and s2["remaining"] == 3

    today.set(dt.date(2027, 1, 2))
    client.post(f"{API}/schedules/generate")
    assert [t["amount"] for t in _txs(client, acc["id"])] == ["-100.00", "-150.00"]
    # Une échéance générée est figée : on ne la change pas par la grille, et la durée ne peut pas passer sous elle.
    bad = client.put(f"{API}/schedules/{s['id']}/occurrences", json={"items": [{"rank": 2, "amount": "-1"}]})
    assert bad.status_code == 409
    assert client.patch(f"{API}/schedules/{s['id']}", json={"count": 1}).status_code == 409
    # Le montant de base change : les échéances sans montant propre suivent, les générées non.
    client.patch(f"{API}/schedules/{s['id']}", json={"amount": "-90"})
    occ = client.get(f"{API}/schedules/{s['id']}/occurrences").json()["items"]
    assert [o["amount"] for o in occ] == ["-100.00", "-150.00", "-150.00"]


def test_installment_own_dates_leave_gaps(client, make, today):
    """10 échéances à partir du 1er juin, étalées sur 12 mois : pas d'échéance en août ni en décembre."""
    acc = make.account()
    s = _schedule(client, account_id=acc["id"], label="Assurance", amount="-60", start_date="2027-06-01",
                  mode="installments", end_mode="count", count=10)
    months = ["06", "07", "09", "10", "11", "28-01", "28-02", "28-03", "28-04", "28-05"]
    dates = [f"2027-{m}-01" if len(m) == 2 else f"20{m[:2]}-{m[3:]}-01" for m in months]
    grid = [{"rank": r, "amount": "-60", "date": d} for r, d in enumerate(dates, start=1)]
    grid[0]["date"] = None  # absente ou nulle : date de la règle
    occ = client.put(f"{API}/schedules/{s['id']}/occurrences", json={"items": grid}).json()["items"]
    assert [o["date"] for o in occ] == dates
    assert [o["own_date"] for o in occ] == [False, False] + [True] * 8

    # Les dates propres valent partout : prochaine échéance, échéances à venir, génération.
    today.set(dt.date(2027, 8, 20))
    up = client.get(f"{API}/schedules/upcoming", params={"days": 15}).json()
    assert [(o["rank"], o["date"]) for o in up] == [(1, "2027-06-01"), (2, "2027-07-01"), (3, "2027-09-01")]
    client.patch(f"{API}/schedules/{s['id']}", json={"generate_from": "2027-06-01"})
    assert sorted(t["date"] for t in _txs(client, acc["id"])) == ["2027-06-01", "2027-07-01"]
    s2 = client.get(f"{API}/schedules/{s['id']}").json()
    assert (s2["next_rank"], s2["next_date"], s2["remaining"]) == (3, "2027-09-01", 8)
    gen = client.post(f"{API}/schedules/{s['id']}/occurrences/3/generate").json()["created"][0]
    assert gen["date"] == "2027-09-01"

    # Sautée puis rétablie : la date propre reste.
    client.post(f"{API}/schedules/{s['id']}/occurrences/4/skip")
    occ = client.delete(f"{API}/schedules/{s['id']}/occurrences/4/skip").json()["items"]
    assert occ[3]["date"] == "2027-10-01" and occ[3]["status"] == "pending"

    # Dates dans l'ordre des rangs ; échéance générée non modifiable.
    bad = [{"rank": 4, "amount": "-60", "date": "2027-10-01"}, {"rank": 5, "amount": "-60", "date": "2027-09-15"}]
    r = client.put(f"{API}/schedules/{s['id']}/occurrences", json={"items": bad})
    assert r.status_code == 422 and "précède" in r.json()["detail"]
    r = client.put(f"{API}/schedules/{s['id']}/occurrences", json={"items": [{"rank": 1, "amount": "-60", "date": "2027-06-02"}]})
    assert r.status_code == 409

    # Grille sans dates : retour aux dates de la règle (les générées gardent la leur).
    occ = client.put(f"{API}/schedules/{s['id']}/occurrences", json={"items": []}).json()["items"]
    assert [o["date"] for o in occ][:4] == ["2027-06-01", "2027-07-01", "2027-09-01", "2027-09-01"]
    assert occ[2]["status"] == "generated" and occ[3]["status"] == "pending"

    # Repasser en périodique efface les dates propres.
    client.put(f"{API}/schedules/{s['id']}/occurrences", json={"items": [{"rank": 10, "amount": "-60", "date": "2029-01-01"}]})
    client.patch(f"{API}/schedules/{s['id']}", json={"mode": "periodic"})
    occ = client.get(f"{API}/schedules/{s['id']}/occurrences").json()["items"]
    assert occ[-1]["date"] == "2028-03-01" and not occ[-1]["own_date"]


def test_rules_are_validated(client, make, today):
    acc = make.account()
    base = {"account_id": acc["id"], "label": "x", "amount": "-1", "start_date": "2026-11-01"}
    assert client.post(f"{API}/schedules", json={**base, "mode": "installments"}).status_code == 422
    assert client.post(f"{API}/schedules", json={**base, "end_mode": "date"}).status_code == 422
    assert client.post(f"{API}/schedules", json={**base, "end_mode": "date", "end_date": "2026-01-01"}).status_code == 422
    assert client.post(f"{API}/schedules", json={**base, "transfer_account_id": acc["id"]}).status_code == 422
    assert client.post(f"{API}/schedules", json={**base, "category_id": 999}).status_code == 404
    periodic = _schedule(client, **base)
    r = client.put(f"{API}/schedules/{periodic['id']}/occurrences", json={"items": []})
    assert r.status_code == 409
    assert client.patch(f"{API}/schedules/{periodic['id']}", json={"label": None}).status_code == 422


def test_delete_keeps_generated_transactions_and_guards_references(client, make, today):
    acc = make.account()
    cat = make.category("Loisirs")
    s = _schedule(client, account_id=acc["id"], label="Club", amount="-20", start_date="2026-10-07", category_id=cat["id"])
    assert client.delete(f"{API}/categories/{cat['id']}").status_code == 409
    assert client.delete(f"{API}/schedules/{s['id']}").status_code == 204
    assert len(_txs(client, acc["id"])) == 1
    assert client.get(f"{API}/schedules/{s['id']}").status_code == 404


def test_generated_transaction_deleted_is_not_regenerated(client, make, today):
    acc = make.account()
    s = _schedule(client, account_id=acc["id"], label="Gym", amount="-30", start_date="2026-10-07")
    tx = _txs(client, acc["id"])[0]
    assert client.delete(f"{API}/transactions/{tx['id']}").status_code == 204
    assert client.post(f"{API}/schedules/generate").json()["created"] == []
    occ = client.get(f"{API}/schedules/{s['id']}/occurrences").json()["items"]
    assert occ[0]["status"] == "generated" and occ[0]["transaction_id"] is None


def test_installments_limited_to_400(client, make, today):
    acc = make.account()
    base = {"account_id": acc["id"], "label": "Prêt", "amount": "-500", "start_date": "2026-11-05", "mode": "installments"}
    assert client.post(f"{API}/schedules", json={**base, "end_mode": "count", "count": 400}).status_code == 201
    r = client.post(f"{API}/schedules", json={**base, "end_mode": "count", "count": 401})
    assert r.status_code == 422 and "400" in r.json()["detail"]
    # par date de fin : 2026-11 → 2060-03 = 401 mensualités
    assert client.post(f"{API}/schedules", json={**base, "end_mode": "date", "end_date": "2060-03-05"}).status_code == 422
    # le mode périodique garde la limite générale
    periodic = client.post(f"{API}/schedules", json={**base, "mode": "periodic", "end_mode": "count", "count": 600})
    assert periodic.status_code == 201
    # passer un périodique de 600 en échéancier : refusé
    assert client.patch(f"{API}/schedules/{periodic.json()['id']}", json={"mode": "installments"}).status_code == 422
