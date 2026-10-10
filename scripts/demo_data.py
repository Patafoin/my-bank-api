#!/usr/bin/env python3
"""Fill an EMPTY My Bank API instance with fictitious demo data.

Used for screenshots and for trying the project out. Every name below is generic and made up.
Standard library only:

    python3 scripts/demo_data.py --url http://127.0.0.1:9931 --token mb_...

Dates are relative to today (about four months of history), so the dashboard always looks current.
"""

import argparse
import datetime as dt
import json
import random
import sys
import urllib.error
import urllib.request

# Libellés français → (anglais, allemand). Choisis par --lang.
LABELS = {
    "Abonnement": ["Subscription", "Abo"],
    "Abonnement mensuel": ["Monthly pass", "Monatskarte"],
    "Abonnements": ["Subscriptions", "Abonnements"],
    "Agence immobilière": ["Letting agency", "Hausverwaltung"],
    "Alimentation": ["Food", "Lebensmittel"],
    "Assurance auto": ["Car insurance", "Kfz-Versicherung"],
    "Assurance habitation": ["Home insurance", "Hausratversicherung"],
    "Assurances": ["Insurance", "Versicherungen"],
    "Assureur": ["Insurer", "Versicherung"],
    "Boulangerie": ["Bakery", "Bäckerei"],
    "Box internet": ["Broadband", "Internet"],
    "Brasserie": ["Bistro", "Bistro"],
    "Cadeau": ["Gift", "Geschenk"],
    "Cadeau commun": ["Joint gift", "Gemeinsames Geschenk"],
    "Caisse d'assurance maladie": ["Health insurance fund", "Krankenkasse"],
    "Carburant": ["Fuel", "Kraftstoff"],
    "Cinéma": ["Cinema", "Kino"],
    "Compte courant": ["Checking account", "Girokonto"],
    "Courses": ["Groceries", "Einkäufe"],
    "Courses et parapharmacie": ["Groceries and pharmacy", "Einkäufe und Drogerie"],
    "Déjeuner": ["Lunch", "Mittagessen"],
    "Électricité": ["Electricity", "Strom"],
    "Électroménager en 4 fois": ["Appliance in 4 instalments", "Haushaltsgerät in 4 Raten"],
    "Employeur": ["Employer", "Arbeitgeber"],
    "Énergie": ["Energy", "Energie"],
    "Épargne": ["Savings", "Sparen"],
    "Épargne du mois": ["Monthly savings", "Monatliches Sparen"],
    "Fournisseur d'énergie": ["Energy supplier", "Energieversorger"],
    "Foyer": ["Household", "Haushalt"],
    "Fruits et légumes": ["Fruit and vegetables", "Obst und Gemüse"],
    "Glaces et cafés": ["Ice creams and coffees", "Eis und Kaffee"],
    "Internet et téléphone": ["Internet and phone", "Internet und Telefon"],
    "Livret d'épargne": ["Savings account", "Sparkonto"],
    "Location": ["Rental", "Unterkunft"],
    "Logement": ["Housing", "Wohnen"],
    "Loisirs": ["Leisure", "Freizeit"],
    "Loyer": ["Rent", "Miete"],
    "Marché": ["Market", "Markt"],
    "Opérateur télécom": ["Telecom operator", "Telefonanbieter"],
    "Parapharmacie": ["Pharmacy", "Drogerie"],
    "Personnel": ["Personal", "Persönlich"],
    "Pharmacie": ["Pharmacy", "Apotheke"],
    "Plein": ["Fill-up", "Tanken"],
    "Porte-monnaie": ["Wallet", "Geldbörse"],
    "Remboursement": ["Refund", "Erstattung"],
    "Remboursements": ["Refunds", "Erstattungen"],
    "Réseau de transport": ["Transit network", "Verkehrsbetrieb"],
    "Restaurant": ["Restaurant", "Restaurant"],
    "Restaurants": ["Restaurants", "Restaurants"],
    "Revenus": ["Earnings", "Einkünfte"],
    "Salaire": ["Salary", "Gehalt"],
    "Santé": ["Health", "Gesundheit"],
    "Séance": ["Screening", "Vorstellung"],
    "Service de streaming": ["Streaming service", "Streamingdienst"],
    "Sorties": ["Outings", "Ausgehen"],
    "Station-service": ["Petrol station", "Tankstelle"],
    "Supermarché": ["Supermarket", "Supermarkt"],
    "Transports": ["Transport", "Verkehr"],
    "Transports en commun": ["Public transport", "Öffentlicher Verkehr"],
    "Travaux": ["Home improvement", "Renovierung"],
    "Viennoiseries": ["Pastries", "Gebäck"],
    "Week-end à la mer": ["Weekend at the seaside", "Wochenende am Meer"],
    "Budget {year}": ["Budget {year}", "Budget {year}"],
}
PAYMENT_METHODS = {
    "Carte bancaire": ["Card", "Karte"],
    "Virement": ["Transfer", "Überweisung"],
    "Chèque": ["Cheque", "Scheck"],
    "Prélèvement": ["Direct debit", "Lastschrift"],
    "Versement": ["Deposit", "Einzahlung"],
}
LANG = "en"


def n(fr: str) -> str:
    """Demo label in the chosen language."""
    return fr if LANG == "fr" else LABELS[fr][0 if LANG == "en" else 1]


class Api:
    def __init__(self, url: str, token: str) -> None:
        self.base = url.rstrip("/") + "/api/v1"
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def call(self, method: str, path: str, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(f"{self.base}/{path}", data=data, method=method, headers=self.headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                text = response.read()
        except urllib.error.HTTPError as err:
            sys.exit(f"{method} {path} → {err.code} {err.read().decode()[:300]}")
        return json.loads(text) if text else None

    def get(self, path):
        return self.call("GET", path)

    def post(self, path, body):
        return self.call("POST", path, body)

    def put(self, path, body):
        return self.call("PUT", path, body)


def money(value: float) -> str:
    return f"{value:.2f}"


def months_back(today: dt.date, n: int) -> dt.date:
    """1st day of the month, n months before the current one."""
    year, month = today.year, today.month - n
    while month <= 0:
        month += 12
        year -= 1
    return dt.date(year, month, 1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", required=True, help="My Bank API base URL, e.g. http://127.0.0.1:9931")
    parser.add_argument("--token", required=True)
    parser.add_argument("--lang", choices=["en", "fr", "de"], default="en", help="Language of the demo labels (default: en)")
    args = parser.parse_args()
    global LANG
    LANG = args.lang
    api = Api(args.url, args.token)
    if api.get("accounts"):
        sys.exit("This instance already has accounts: demo data is only loaded into an empty instance.")

    rng = random.Random(42)
    today = dt.date.today()
    start = months_back(today, 3)

    methods = {m["name"]: m["id"] for m in api.get("payment-methods")}
    if LANG != "fr":  # comme l'intégration à la première installation : modes de règlement dans la langue choisie
        for name, ident in methods.items():
            if name in PAYMENT_METHODS:
                api.call("PATCH", f"payment-methods/{ident}", {"name": PAYMENT_METHODS[name][0 if LANG == "en" else 1]})
    card, transfer_method, direct_debit = (
        methods.get("Carte bancaire"),
        methods.get("Virement"),
        methods.get("Prélèvement"),
    )

    # --- Comptes -------------------------------------------------------------------------------
    checking = api.post(
        "accounts",
        {"name": n("Compte courant"), "kind": "checking", "opening_balance": "1850.00", "opening_date": str(start)},
    )
    savings = api.post(
        "accounts",
        {"name": n("Livret d'épargne"), "kind": "savings", "opening_balance": "4200.00", "opening_date": str(start)},
    )
    api.post("accounts", {"name": n("Porte-monnaie"), "kind": "cash", "opening_balance": "60.00", "opening_date": str(start)})

    # --- Rubriques (arborescence) --------------------------------------------------------------
    def category(name, parent=None, kind=None):
        body = {"name": name, "parent_id": parent["id"] if parent else None}
        if parent is None:
            body["kind"] = kind
        return api.post("categories", body)

    income = category(n("Revenus"), kind="income")
    salary = category(n("Salaire"), income)
    refunds = category(n("Remboursements"), income)
    home = category(n("Logement"), kind="expense")
    rent = category(n("Loyer"), home)
    energy = category(n("Énergie"), home)
    internet = category(n("Internet et téléphone"), home)
    food = category(n("Alimentation"), kind="expense")
    groceries = category(n("Courses"), food)
    restaurants = category(n("Restaurants"), food)
    transport = category(n("Transports"), kind="expense")
    fuel = category(n("Carburant"), transport)
    public_transport = category(n("Transports en commun"), transport)
    leisure = category(n("Loisirs"), kind="expense")
    outings = category(n("Sorties"), leisure)
    subscriptions = category(n("Abonnements"), leisure)
    health = category(n("Santé"), kind="expense")
    insurance = category(n("Assurances"), kind="expense")
    saving = category(n("Épargne"), kind="savings")

    # --- Classes (second axe) -----------------------------------------------------------------
    personal = api.post("classes", {"name": n("Personnel")})
    household = api.post("classes", {"name": n("Foyer")})
    api.post("classes", {"name": n("Travaux"), "parent_id": household["id"]})

    # --- Tiers ---------------------------------------------------------------------------------
    def payee(name, default=None):
        return api.post("payees", {"name": name, "default_category_id": default["id"] if default else None})

    employer = payee(n("Employeur"), salary)
    landlord = payee(n("Agence immobilière"), rent)
    energy_co = payee(n("Fournisseur d'énergie"), energy)
    telecom = payee(n("Opérateur télécom"), internet)
    supermarket = payee(n("Supermarché"), groceries)
    market = payee(n("Marché"), groceries)
    bakery = payee(n("Boulangerie"), groceries)
    station = payee(n("Station-service"), fuel)
    transit = payee(n("Réseau de transport"), public_transport)
    cinema = payee(n("Cinéma"), outings)
    streaming = payee(n("Service de streaming"), subscriptions)
    pharmacy = payee(n("Pharmacie"), health)
    insurer = payee(n("Assureur"), insurance)
    bistro = payee(n("Brasserie"), restaurants)
    health_fund = payee(n("Caisse d'assurance maladie"), refunds)

    # --- Opérations ----------------------------------------------------------------------------
    def tx(day, amount, who, cat, method=card, label=None, cls=None, account=checking, state=0, splits=None):
        body = {
            "account_id": account["id"],
            "date": str(day),
            "amount": money(amount),
            "payee_id": who["id"],
            "payment_method_id": method,
            "label": label,
            "state": state,
        }
        if splits:
            body["splits"] = splits
        else:
            body["category_id"] = cat["id"]
            body["class_id"] = cls["id"] if cls else None
        return api.post("transactions", body)

    month = start
    while month <= today:
        # Les mois révolus sont rapprochés, le mois précédent pointé, le mois en cours non pointé.
        age = (today.year - month.year) * 12 + today.month - month.month
        state = 2 if age >= 2 else 1 if age == 1 else 0

        def on(day, month=month):
            return month.replace(day=min(day, 28))

        def due(day, month=month):
            return on(day, month) <= today

        if due(1):
            tx(on(1), 2350, employer, salary, transfer_method, n("Salaire"), personal, state=state)
        if due(3):
            tx(on(3), -780, landlord, rent, direct_debit, n("Loyer"), household, state=state)
        if due(6):
            tx(on(6), -rng.choice([62.40, 71.15, 84.90]), energy_co, energy, direct_debit, n("Électricité"), household, state=state)
        if due(8):
            tx(on(8), -34.99, telecom, internet, direct_debit, n("Box internet"), household, state=state)
        if due(10):
            tx(on(10), -13.49, streaming, subscriptions, card, n("Abonnement"), personal, state=state)
        if due(12):
            tx(on(12), -42.10, insurer, insurance, direct_debit, n("Assurance habitation"), household, state=state)
        for day in (2, 9, 16, 23):
            if due(day):
                tx(on(day), -round(rng.uniform(55, 120), 2), supermarket, groceries, label=n("Courses"), cls=household, state=state)
        for day in (5, 19):
            if due(day):
                tx(on(day), -round(rng.uniform(4, 12), 2), bakery, groceries, state=state)
        if due(13):
            tx(on(13), -round(rng.uniform(18, 35), 2), market, groceries, label=n("Fruits et légumes"), state=state)
        if due(11):
            tx(on(11), -round(rng.uniform(55, 75), 2), station, fuel, label=n("Plein"), cls=personal, state=state)
        if due(4):
            tx(on(4), -38.80, transit, public_transport, label=n("Abonnement mensuel"), cls=personal, state=state)
        if due(20):
            tx(on(20), -round(rng.uniform(25, 60), 2), bistro, restaurants, label=n("Déjeuner"), state=state)
        if due(22):
            tx(on(22), -21.00, cinema, outings, label=n("Séance"), state=state)
        if due(17):
            # Une opération ventilée : courses et pharmacie au même passage en caisse.
            tx(
                on(17),
                -86.40,
                supermarket,
                None,
                label=n("Courses et parapharmacie"),
                state=state,
                splits=[
                    {"amount": "-68.20", "category_id": groceries["id"], "class_id": household["id"], "label": n("Courses")},
                    {"amount": "-18.20", "category_id": health["id"], "label": n("Parapharmacie")},
                ],
            )
        if due(18) and age == 1:
            tx(on(18), -27.50, pharmacy, health, state=state)
            tx(on(24), 18.90, health_fund, refunds, transfer_method, n("Remboursement"), state=state)
        if due(15):
            api.post(
                "transfers",
                {
                    "from_account_id": checking["id"],
                    "to_account_id": savings["id"],
                    "date": str(on(15)),
                    "amount": "200.00",
                    "payment_method_id": transfer_method,
                    "label": n("Épargne du mois"),
                    "category_id": saving["id"],
                    "from_state": state,
                    "to_state": state,
                },
            )
        month = months_back(month, -1)

    # Petites dépenses en espèces.
    cash = next(a for a in api.get("accounts") if a["kind"] == "cash")
    for back in (3, 9, 16):
        day = today - dt.timedelta(days=back)
        tx(day, -round(rng.uniform(2, 9), 2), bakery, groceries, None, n("Viennoiseries"), account=cash)

    # --- Opérations planifiées -----------------------------------------------------------------
    next_month = months_back(today, -1)
    future = {"generate_from": str(next_month)}  # ne pas rejouer les mois déjà saisis ci-dessus
    api.post(
        "schedules",
        {"account_id": checking["id"], "label": n("Loyer"), "start_date": str(next_month.replace(day=3)), "amount": "-780.00",
         "payee_id": landlord["id"], "payment_method_id": direct_debit, "category_id": rent["id"], "lead_days": 5, **future},
    )
    api.post(
        "schedules",
        {"account_id": checking["id"], "label": n("Salaire"), "start_date": str(next_month), "amount": "2350.00",
         "payee_id": employer["id"], "payment_method_id": transfer_method, "category_id": salary["id"], **future},
    )
    api.post(
        "schedules",
        {"account_id": checking["id"], "label": n("Épargne du mois"), "start_date": str(next_month.replace(day=15)),
         "amount": "-200.00", "transfer_account_id": savings["id"], "category_id": saving["id"], **future},
    )
    api.post(
        "schedules",
        {"account_id": checking["id"], "label": n("Assurance auto"), "start_date": str(next_month.replace(day=10)),
         "amount": "-96.00", "unit": "quarter", "payee_id": insurer["id"], "category_id": insurance["id"], **future},
    )
    plan = api.post(
        "schedules",
        {"account_id": checking["id"], "label": n("Électroménager en 4 fois"), "mode": "installments",
         "start_date": str(next_month.replace(day=5)), "amount": "-112.50", "end_mode": "count", "count": 4,
         "payee_id": supermarket["id"], "category_id": home["id"], **future},
    )
    api.put(
        f"schedules/{plan['id']}/occurrences",
        {"items": [{"rank": 1, "amount": "-112.53"}] + [{"rank": r, "amount": "-112.49"} for r in (2, 3, 4)]},
    )

    # --- Budget de l'année ---------------------------------------------------------------------
    budget = api.post(
        "budgets", {"account_id": checking["id"], "name": n("Budget {year}").format(year=today.year), "start_month": f"{today.year}-01", "months": 12}
    )
    planned = [
        (salary, 2350), (rent, 780), (energy, 80), (internet, 35), (groceries, 450), (restaurants, 60), (fuel, 65),
        (public_transport, 39), (outings, 40), (subscriptions, 14), (health, 30), (insurance, 75), (saving, 200),
    ]
    lines = [
        {"category_id": cat["id"], "month": f"{today.year}-{m:02d}", "amount": money(amount)}
        for cat, amount in planned
        for m in range(1, 13)
    ]
    api.put(f"budgets/{budget['id']}/lines", {"lines": lines})

    # --- Comptes partagés ----------------------------------------------------------------------
    trip = api.post("shared-accounts", {"name": n("Week-end à la mer"), "participants": ["Alice", "Bruno", "Chloé"]})
    alice, bruno, chloe = (p["id"] for p in trip["participants"])
    for who, amount, label, back in (
        (alice, "186.00", n("Location"), 12),
        (bruno, "64.30", n("Courses"), 11),
        (chloe, "48.00", n("Restaurant"), 11),
        (bruno, "22.50", n("Glaces et cafés"), 10),
    ):
        api.post(
            f"shared-accounts/{trip['id']}/expenses",
            {"participant_id": who, "amount": amount, "label": label, "date": str(today - dt.timedelta(days=back))},
        )
    dinner = api.post("shared-accounts", {"name": n("Cadeau commun"), "participants": ["Alice", "Bruno"]})
    api.post(
        f"shared-accounts/{dinner['id']}/expenses",
        {"participant_id": dinner["participants"][0]["id"], "amount": "90.00", "label": n("Cadeau"),
         "date": str(today - dt.timedelta(days=40))},
    )
    api.call("PATCH", f"shared-accounts/{dinner['id']}", {"closed": True})

    accounts = api.get("accounts")
    print("Demo data loaded:", ", ".join(f"{a['name']} {a['balances']['total']}" for a in accounts))


if __name__ == "__main__":
    main()
