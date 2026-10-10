"""Import des exports de MS Comptes Bancaires : fichiers QIF (un par compte) et liste des tiers (CSV)."""

import csv
import datetime as dt
import io
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import trees
from .balances import account_balances
from .i18n import tr
from .models import Account, AnalysisClass, Category, Payee, PaymentMethod, Split, Transaction, TxState
from .money import from_cents, parse_amount, to_cents

STATES = {"": TxState.NONE, "*": TxState.POINTED, "C": TxState.POINTED, "X": TxState.RECONCILED, "R": TxState.RECONCILED}
DATE_FORMATS = ("%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d")


class ImportFileError(ValueError):
    """Erreur de contenu dans un fichier importé (message destiné à l'utilisateur)."""


def decode(raw: bytes) -> str:
    """MS Comptes Bancaires exporte en Windows-1252 ; on accepte aussi l'UTF-8."""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252")


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold().strip()


@dataclass
class QifSplit:
    category: str | None
    amount: int
    label: str | None


@dataclass
class QifRecord:
    line: int
    date: dt.date
    amount: int
    state: int = TxState.NONE
    payee: str | None = None
    method: str | None = None
    number: str | None = None
    label: str | None = None
    classes: list[str] = field(default_factory=list)
    splits: list[QifSplit] = field(default_factory=list)
    transfer_to: str | None = None
    partial_split: bool = False


def _parse_date(value: str, line: int) -> dt.date:
    value = value.strip().replace("'", "/").replace(" ", "")
    for fmt in DATE_FORMATS:
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ImportFileError(tr("ligne {line} : date illisible « {value} »", line=line, value=value))


def _parse_cents(value: str, line: int) -> int:
    try:
        return to_cents(parse_amount(value))
    except ValueError as err:
        raise ImportFileError(tr("ligne {line} : montant illisible « {value} »", line=line, value=value)) from err


def _bracket(value: str | None) -> str | None:
    if value and value.startswith("[") and value.endswith("]"):
        return value[1:-1].strip()
    return None


def parse_qif(text: str) -> list[QifRecord]:
    records: list[QifRecord] = []
    current: dict = {}
    splits: list[dict] = []
    start_line = 1

    def flush(line_no: int) -> None:
        nonlocal current, splits
        if "D" in current or "T" in current:
            if "D" not in current or "T" not in current:
                raise ImportFileError(tr("ligne {line} : opération sans date ou sans montant", line=start_line))
            record = QifRecord(
                line=start_line,
                date=_parse_date(current["D"], start_line),
                amount=_parse_cents(current["T"], start_line),
                state=STATES.get(current.get("C", "").strip().upper(), TxState.NONE),
                payee=current.get("P") or None,
                label=current.get("M") or None,
                classes=current.get("A", []),
            )
            n = current.get("N")
            if n:
                if re.fullmatch(r"\d+", n):
                    record.number = n
                else:
                    record.method = n
            category = current.get("L")
            if _bracket(category):
                record.transfer_to, category = _bracket(category), None
            if splits:
                for s in splits:
                    if "$" not in s:
                        raise ImportFileError(tr("ligne {line} : ligne de ventilation sans montant", line=start_line))
                    cat = s.get("S") or None
                    if _bracket(cat):
                        record.transfer_to, cat = _bracket(cat), None
                    record.splits.append(QifSplit(cat, _parse_cents(s["$"], start_line), s.get("E") or None))
                total = sum(s.amount for s in record.splits)
                if total != record.amount:
                    # MS tolère une ventilation partielle : le reste devient une ligne sans rubrique.
                    record.splits.append(QifSplit(None, record.amount - total, None))
                    record.partial_split = True
            else:
                record.splits.append(QifSplit(category, record.amount, None))
            records.append(record)
        current, splits = {}, []

    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip("\r")
        if not line.strip():
            continue
        if line.startswith("!"):
            kind = line[1:].strip().lower()
            if kind.startswith("type:") and kind[5:] not in {"bank", "cash", "ccard", "oth a", "oth l"}:
                raise ImportFileError(tr("ligne {line} : type de compte QIF non pris en charge ({type})", line=line_no, type=line.strip()))
            continue
        code, value = line[0], line[1:].strip()
        if code == "^":
            flush(line_no)
            start_line = line_no + 1
            continue
        if not current and not splits:
            start_line = line_no
        if code == "S":
            splits.append({"S": value})
        elif code in "E$" and splits and code not in splits[-1]:
            splits[-1][code] = value
        elif code == "E" and splits:
            splits[-1]["E"] = f"{splits[-1]['E']} {value}".strip()
        elif code == "A":
            if value:
                current.setdefault("A", []).append(value)
        elif code == "U":
            current.setdefault("T", value)
        else:
            current[code] = value
    flush(len(text.splitlines()))
    return records


# --- Tiers (tiers.csv) ---------------------------------------------------------------------------

PAYEE_COLUMNS = {
    "Abrev": "short_name",
    "Adr1": "address1",
    "Adr2": "address2",
    "Adr3": "address3",
    "Cp": "postal_code",
    "Ville": "city",
    "Region": "region",
    "Pays": "country",
    "Tel": "phone",
    "Tel2": "phone2",
    "Mobile": "mobile",
    "Fax": "fax",
    "Fax2": "fax2",
    "Email": "email",
    "URL": "url",
    "Rmq": "notes",
    "NomBqe": "bank_name",
    "Codebqe": "bank_code",
    "Guichetbqe": "branch_code",
    "NumCpte": "account_number",
    "Clerib": "rib_key",
    "IBAN": "iban",
    "SWIFT": "swift",
    "Libre1": "free1",
    "Libre2": "free2",
    "Libre3": "free3",
}


def parse_payees(text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    if not reader.fieldnames or "Lib" not in reader.fieldnames:
        raise ImportFileError(tr("tiers : colonne « Lib » introuvable (export CSV des tiers de MS Comptes Bancaires attendu)"))
    rows = []
    for i, row in enumerate(reader, start=2):
        name = (row.get("Lib") or "").strip()
        if not name:
            raise ImportFileError(tr("tiers, ligne {line} : nom vide", line=i))
        values = {"name": name, "external_code": (row.get("Code") or "").strip() or None}
        for column, attr in PAYEE_COLUMNS.items():
            value = (row.get(column) or "").strip()
            if value:
                values[attr] = value
        values["active"] = (row.get("Actif") or "1").strip() != "0"
        categ = (row.get("Categ") or "").strip()
        if categ and categ != "0":
            values["payee_category"] = categ
        rub = (row.get("Rub") or "").strip()
        if rub:
            values["_default_category"] = rub
        rows.append(values)
    return rows


# --- Import complet ------------------------------------------------------------------------------


@dataclass
class QifFile:
    account: str
    content: str


def _account_kind(name: str) -> str:
    n = _norm(name)
    if any(w in n for w in ("livret", "epargne", "ldd", "pel", "cel")):
        return "savings"
    if "carte" in n:
        return "card"
    return "checking"


def _category_kind(root: str, net: int) -> str:
    if _norm(root).startswith("epargne"):
        return "savings"
    return "income" if net > 0 else "expense"


def run_import(session: Session, files: list[QifFile], payees_csv: str | None = None) -> dict:
    """Import dans la session courante ; l'appelant valide (commit) ou annule (simulation)."""
    if not files and payees_csv is None:
        raise ImportFileError(tr("rien à importer"))
    names = [f.account.strip() for f in files]
    if len(set(map(_norm, names))) != len(names):
        raise ImportFileError(tr("un même compte apparaît deux fois dans l'import"))
    parsed: dict[str, list[QifRecord]] = {}
    for f in files:
        try:
            parsed[f.account.strip()] = parse_qif(f.content)
        except ImportFileError as err:
            raise ImportFileError(f"{f.account} : {err}") from err

    report: dict = {"accounts": [], "created": defaultdict(int), "warnings": []}
    for name, records in parsed.items():
        for r in records:
            if r.partial_split:
                rest = r.splits[-1].amount
                report["warnings"].append(
                    f"{name}, {r.date:%d/%m/%Y} « {r.label or r.payee or ''} » : ventilation incomplète, "
                    f"reste de {from_cents(rest)} placé sur une ligne sans rubrique"
                )

    # Comptes : créés s'ils n'existent pas, et forcément vides (import de migration).
    accounts: dict[str, Account] = {}
    for name in parsed:
        account = session.scalar(select(Account).where(func.lower(Account.name) == name.lower()))
        if account is None:
            account = Account(name=name, kind=_account_kind(name))
            session.add(account)
            session.flush()
            report["created"]["accounts"] += 1
        elif session.scalar(select(func.count()).where(Transaction.account_id == account.id)):
            raise ImportFileError(tr("le compte « {name} » contient déjà des opérations : import refusé", name=account.name))
        accounts[name] = account

    # Rubriques : chemin « Parent:Enfant », type déduit (Épargne, sinon signe du total).
    nets: dict[str, int] = defaultdict(int)
    for records in parsed.values():
        for r in records:
            for s in r.splits:
                if s.category:
                    nets[s.category.split(":")[0].strip()] += s.amount
    category_ids: dict[str, int] = {}
    nodes = trees.load(session, Category)
    by_path = {path.replace(" > ", ":").casefold(): node for node, path, _ in trees.ordered(nodes)}
    all_paths = sorted({s.category for rs in parsed.values() for r in rs for s in r.splits if s.category})
    for full in all_paths:
        parts = [p.strip() for p in full.split(":") if p.strip()]
        parent = None
        for depth in range(len(parts)):
            key = ":".join(parts[: depth + 1]).casefold()
            node = by_path.get(key)
            if node is None:
                kind = parent.kind if parent else _category_kind(parts[0], nets[parts[0]])
                node = Category(name=parts[depth], parent_id=parent.id if parent else None, kind=kind)
                session.add(node)
                session.flush()
                by_path[key] = node
                report["created"]["categories"] += 1
            parent = node
        category_ids[full] = parent.id

    # Classes (champ « A » de MS, hypothèse H4) : une classe racine par valeur.
    class_ids: dict[str, int] = {}
    existing_classes = {c.name.casefold(): c for c in session.scalars(select(AnalysisClass).where(AnalysisClass.parent_id.is_(None)))}
    for name in sorted({c for rs in parsed.values() for r in rs for c in r.classes}):
        node = existing_classes.get(name.casefold())
        if node is None:
            node = AnalysisClass(name=name)
            session.add(node)
            session.flush()
            report["created"]["classes"] += 1
        class_ids[name] = node.id

    # Modes de règlement.
    method_ids = {m.name.casefold(): m.id for m in session.scalars(select(PaymentMethod))}
    for name in sorted({r.method for rs in parsed.values() for r in rs if r.method}):
        if name.casefold() not in method_ids:
            method = PaymentMethod(name=name)
            session.add(method)
            session.flush()
            method_ids[name.casefold()] = method.id
            report["created"]["payment_methods"] += 1

    # Tiers : liste complète d'abord (fiches), puis ceux des opérations absents de la liste.
    payee_ids = {p.name.casefold(): p.id for p in session.scalars(select(Payee))}
    if payees_csv is not None:
        for values in parse_payees(payees_csv):
            default_category = values.pop("_default_category", None)
            if values["name"].casefold() in payee_ids:
                report["warnings"].append(f"tiers « {values['name']} » déjà présent : fiche conservée telle quelle")
                continue
            if default_category:
                values["default_category_id"] = category_ids.get(default_category) or (
                    by_path[default_category.casefold()].id if default_category.casefold() in by_path else None
                )
            payee = Payee(**values)
            session.add(payee)
            session.flush()
            payee_ids[payee.name.casefold()] = payee.id
            report["created"]["payees"] += 1
    for name in sorted({r.payee for rs in parsed.values() for r in rs if r.payee}):
        if name.casefold() not in payee_ids:
            payee = Payee(name=name)
            session.add(payee)
            session.flush()
            payee_ids[name.casefold()] = payee.id
            report["created"]["payees"] += 1

    # Virements : MS marque les deux jambes avec le nom d'un des comptes importés comme tiers.
    account_by_norm = {_norm(n): n for n in parsed}
    candidates: dict[str, list[QifRecord]] = defaultdict(list)
    for name, records in parsed.items():
        for r in records:
            target = r.transfer_to or r.payee
            if target and _norm(target) in account_by_norm:
                candidates[name].append(r)
    pairs: list[tuple[str, QifRecord, str, QifRecord]] = []
    paired: set[int] = set()
    for name, records in candidates.items():
        for r in records:
            if id(r) in paired:
                continue
            target = account_by_norm[_norm(r.transfer_to or r.payee)]
            others = [target] if target != name else [n for n in parsed if n != name]
            for other in others:
                match = next(
                    (
                        o
                        for o in candidates.get(other, [])
                        if id(o) not in paired and o.date == r.date and o.amount == -r.amount
                    ),
                    None,
                )
                if match is not None:
                    paired.update({id(r), id(match)})
                    pairs.append((name, r, other, match))
                    break
    unmatched = sum(1 for rs in candidates.values() for r in rs if id(r) not in paired)
    if unmatched:
        report["warnings"].append(
            f"{unmatched} opération(s) ont pour tiers le nom d'un compte mais aucune opération jumelle n'a été "
            "trouvée (même date, montant opposé) : importées comme opérations simples"
        )

    # Opérations.
    created: dict[int, Transaction] = {}
    for name, records in parsed.items():
        account = accounts[name]
        for r in sorted(records, key=lambda x: (x.date, x.line)):
            is_transfer = id(r) in paired
            class_id = class_ids[r.classes[0]] if r.classes else None
            splits = [
                Split(
                    amount=s.amount,
                    category_id=category_ids.get(s.category) if s.category else None,
                    class_id=class_id,
                    label=None if s.label == r.label else s.label,
                    position=i,
                )
                for i, s in enumerate(r.splits)
            ]
            tx = Transaction(
                account_id=account.id,
                date=r.date,
                amount=r.amount,
                payee_id=None if is_transfer else (payee_ids[r.payee.casefold()] if r.payee else None),
                payment_method_id=method_ids[r.method.casefold()] if r.method else None,
                number=r.number,
                label=r.label,
                state=int(r.state),
                splits=splits,
            )
            session.add(tx)
            created[id(r)] = tx
    session.flush()
    for _, r, _, match in pairs:
        a, b = created[id(r)], created[id(match)]
        a.transfer_peer_id, b.transfer_peer_id = b.id, a.id
    session.flush()

    today = dt.date.today()
    for name, records in parsed.items():
        account = accounts[name]
        states = defaultdict(int)
        for r in records:
            states[int(r.state)] += 1
        report["accounts"].append(
            {
                "account_id": account.id,
                "name": account.name,
                "transactions": len(records),
                "not_pointed": states[TxState.NONE],
                "pointed": states[TxState.POINTED],
                "reconciled": states[TxState.RECONCILED],
                "future": sum(1 for r in records if r.date > today),
                "first_date": min((r.date for r in records), default=None),
                "last_date": max((r.date for r in records), default=None),
                "balances": {k: f"{v:.2f}" for k, v in account_balances(session, account).items()},
            }
        )
    report["transfers_paired"] = len(pairs)
    report["created"] = dict(report["created"])
    return report
