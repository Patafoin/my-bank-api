import datetime as dt
import random
import time

import pytest
from sqlalchemy import insert

from my_bank_api.models import Split, Transaction

N = 100_000
API = "/api/v1"


def timed(fn, repeat=5):
    best = float("inf")
    for _ in range(repeat):
        start = time.perf_counter()
        response = fn()
        best = min(best, time.perf_counter() - start)
        assert response.status_code < 300, response.text
    return best * 1000, response


@pytest.mark.slow
def test_100k_operations(client, app, make):
    cc = make.account("Compte courant")
    other = make.account("Livret A")
    cat = make.category("Divers")
    rng = random.Random(42)
    first = dt.date(1990, 1, 1)
    span = (dt.date(2026, 12, 31) - first).days
    rows = [
        {
            "id": i,
            "account_id": cc["id"] if rng.random() < 0.75 else other["id"],
            "date": first + dt.timedelta(days=rng.randint(0, span)),
            "amount": rng.randint(-20000, 5000),
            "state": rng.choices([0, 1, 2], [1, 1, 30])[0],
        }
        for i in range(1, N + 1)
    ]
    with app.state.sessionmaker() as session:
        session.execute(insert(Transaction), rows)
        session.execute(
            insert(Split), [{"transaction_id": r["id"], "amount": r["amount"], "category_id": cat["id"]} for r in rows]
        )
        session.commit()

    oldest = min(
        (r for r in rows if r["account_id"] == cc["id"] and r["state"] != 2), key=lambda r: (r["date"], r["id"])
    )
    results = {
        "comptes + 3 soldes": timed(lambda: client.get(f"{API}/accounts")),
        "3 soldes d'un compte": timed(lambda: client.get(f"{API}/accounts/{cc['id']}/balances")),
        "liste 3 mois + solde/ligne": timed(
            lambda: client.get(
                f"{API}/accounts/{cc['id']}/transactions", params={"start": "2026-07-01", "end": "2026-09-30"}
            )
        ),
        "modif opé la plus ancienne": timed(
            lambda: client.patch(f"{API}/transactions/{oldest['id']}", json={"label": "x", "amount": "-1.23"}),
        ),
        "saisie": timed(lambda: client.post(f"{API}/transactions", json={"account_id": cc["id"], "date": "2026-10-06", "amount": "-1"})),
    }
    report = " | ".join(f"{name} {ms:.1f} ms" for name, (ms, _) in results.items())
    print(f"\n{N:,} opérations : {report}")
    for name, (ms, _) in results.items():
        assert ms < 500, f"{name} trop lent : {ms:.0f} ms"
