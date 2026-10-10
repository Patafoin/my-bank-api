import base64
import datetime as dt

import pytest

from my_bank_api.ms_import import ImportFileError, decode, parse_payees, parse_qif

API = "/api/v1"

CC = """!Type:Bank
D30/10/2023
T300
NVirement
PCompte joint
CX
MAlimentation initiale
^
D06/11/2023
T-14,8
NCarte bancaire
PRestaurant
C*
SSorties
$-14,8
MDîner
^
D07/11/2023
T-10
NVirement
PLivret A
CX
SEpargne
$-10
EVirement initial
MVirement initial
^
D07/07/2024
T-60
NCarte bancaire
PDAB
SAchats divers
$-15
ETHPS
SAlimentation:Marché
$-45
EMarché
MRetrait
AAlice
^
D15/01/2099
T-5
NPrélèvement
PStreaming
SLoisirs:Abonnements
$-5
^
D02/02/2024
T2000
NVirement
PEmployeur
CX
SSalaire
$2000
^
"""

LIVRET = """!Type:Bank
D07/11/2023
T10
NVirement
PLivret A
CX
SEpargne
$10
MVirement initial
^
D31/12/2023
T1,5
NVirement
PBanque en ligne
CX
MIntérêts
^
"""

TIERS = "Code;Lib;Abrev;Rub;Adr1;Ville;IBAN;Categ;Actif\n" + "\n".join(
    [
        '"1";"Compte joint";"";"";"";"";"";"0";"1"',
        '"2";"Restaurant";"Resto";"";"1 rue X";"Rennes";"FR76";"0";"1"',
        '"3";"Ancien";"";"";"";"";"";"0";"0"',
    ]
)


def b64(text: str, encoding="cp1252") -> str:
    return base64.b64encode(text.encode(encoding)).decode()


def body(dry_run=True, **extra):
    return {
        "files": [
            {"account": "Compte courant", "content_base64": b64(CC)},
            {"account": "Livret A", "content_base64": b64(LIVRET)},
        ],
        "payees_csv_base64": b64(TIERS),
        "dry_run": dry_run,
        **extra,
    }


def test_decode_cp1252_and_utf8():
    assert decode("Marché".encode("cp1252")) == "Marché"
    assert decode("Marché".encode()) == "Marché"


def test_parse_qif_fields():
    records = parse_qif(CC)
    assert len(records) == 6
    first, resto, livret, dab = records[:4]
    assert first.date == dt.date(2023, 10, 30) and first.amount == 30000 and first.state == 2
    assert first.method == "Virement" and first.label == "Alimentation initiale"
    assert first.splits[0].category is None
    assert resto.amount == -1480 and resto.state == 1 and resto.splits[0].category == "Sorties"
    assert dab.classes == ["Alice"] and dab.state == 0
    assert [(s.category, s.amount, s.label) for s in dab.splits] == [
        ("Achats divers", -1500, "THPS"),
        ("Alimentation:Marché", -4500, "Marché"),
    ]


def test_partial_split_gets_uncategorized_remainder():
    record = parse_qif("!Type:Bank\nD01/01/2024\nT-1404\nSLoyer\n$-424\n^\n")[0]
    assert record.partial_split
    assert [(s.category, s.amount) for s in record.splits] == [("Loyer", -42400), (None, -98000)]


def test_parse_qif_errors():
    with pytest.raises(ImportFileError, match="date illisible"):
        parse_qif("!Type:Bank\nD2024/31/12\nT-10\n^\n")
    with pytest.raises(ImportFileError, match="non pris en charge"):
        parse_qif("!Type:Invst\nD01/01/2024\nT-10\n^\n")
    with pytest.raises(ImportFileError, match="sans date"):
        parse_qif("!Type:Bank\nT-10\n^\n")


def test_parse_qif_numeric_n_is_cheque_number():
    record = parse_qif("!Type:Bank\nD01/01/2024\nT-10\nN1234567\n^\n")[0]
    assert record.number == "1234567" and record.method is None


def test_parse_payees():
    rows = parse_payees(TIERS)
    assert rows[1] == {
        "name": "Restaurant",
        "external_code": "2",
        "short_name": "Resto",
        "address1": "1 rue X",
        "city": "Rennes",
        "iban": "FR76",
        "active": True,
    }
    assert rows[2]["active"] is False


def test_dry_run_changes_nothing(client):
    r = client.post(f"{API}/import/ms", json=body())
    assert r.status_code == 200, r.text
    report = r.json()
    assert report["dry_run"] is True and report["transfers_paired"] == 1
    assert client.get(f"{API}/accounts").json() == []
    assert client.get(f"{API}/payees").json() == []


def test_import_matches_ms_semantics(client):
    report = client.post(f"{API}/import/ms", json=body(dry_run=False)).json()
    cc, livret = report["accounts"]
    # total inclut le futur ; pointé = * et X ; rapproché = X seulement
    assert cc["balances"] == {"total": "2210.20", "pointed": "2275.20", "reconciled": "2290.00"}
    assert (cc["not_pointed"], cc["pointed"], cc["reconciled"], cc["future"]) == (2, 1, 3, 1)
    assert livret["balances"] == {"total": "11.50", "pointed": "11.50", "reconciled": "11.50"}
    assert report["created"] == {
        "accounts": 2,
        "categories": 8,  # Sorties, Epargne, Achats divers, Alimentation (+ Marché), Loisirs (+ Abonnements), Salaire
        "classes": 1,
        "payees": 8,  # 3 de la liste + Livret A, DAB, Streaming, Employeur, Banque en ligne
    }
    accounts = {a["name"]: a for a in client.get(f"{API}/accounts").json()}
    assert accounts["Livret A"]["kind"] == "savings" and accounts["Compte courant"]["kind"] == "checking"


def test_import_transfer_and_categories(client):
    client.post(f"{API}/import/ms", json=body(dry_run=False))
    cats = {c["path"]: c for c in client.get(f"{API}/categories").json()}
    assert cats["Epargne"]["kind"] == "savings"
    assert cats["Salaire"]["kind"] == "income"
    assert cats["Alimentation > Marché"]["kind"] == "expense"
    assert cats["Loisirs > Abonnements"]["depth"] == 1
    transfers = [t for t in client.get(f"{API}/transactions").json()["items"] if t["transfer_peer_id"]]
    assert len(transfers) == 2
    assert {t["amount"] for t in transfers} == {"-10.00", "10.00"}
    assert all(t["payee_id"] is None and t["splits"][0]["category_id"] == cats["Epargne"]["id"] for t in transfers)
    dab = client.get(f"{API}/transactions", params={"q": "Retrait"}).json()["items"][0]
    assert len(dab["splits"]) == 2 and dab["splits"][0]["class_id"] is not None
    assert dab["splits"][0]["label"] == "THPS"
    payees = {p["name"]: p for p in client.get(f"{API}/payees").json()}
    assert payees["Restaurant"]["city"] == "Rennes" and payees["Ancien"]["active"] is False
    assert "Livret A" in payees  # tiers présent dans les opérations, conservé même s'il sert de marqueur de virement


def test_import_refused_on_non_empty_account(client, make):
    acc = make.account("Livret A")
    make.tx(acc["id"], "1")
    r = client.post(f"{API}/import/ms", json=body(dry_run=True))
    assert r.status_code == 422 and "contient déjà" in r.json()["detail"]


def test_import_bad_content(client):
    bad = {"files": [{"account": "X", "content_base64": b64("!Type:Bank\nD01/01/2024\nTabc\n^\n")}]}
    r = client.post(f"{API}/import/ms", json=bad)
    assert r.status_code == 422 and "montant illisible" in r.json()["detail"]
    r = client.post(f"{API}/import/ms", json={"files": [{"account": "X", "content_base64": "%%%"}]})
    assert r.status_code == 422


def test_payee_by_name_on_transaction(client, make):
    acc = make.account()
    t1 = make.tx(acc["id"], "-3", payee_name="Boulangerie")
    t2 = make.tx(acc["id"], "-4", payee_name="boulangerie ")
    assert t1["payee_id"] == t2["payee_id"] is not None
    r = client.patch(f"{API}/transactions/{t1['id']}", json={"payee_name": "Pâtisserie"})
    assert r.json()["payee_id"] != t2["payee_id"]
    bad = {"account_id": acc["id"], "date": "2026-01-01", "amount": "-1", "payee_id": 1, "payee_name": "x"}
    assert client.post(f"{API}/transactions", json=bad).status_code == 422
