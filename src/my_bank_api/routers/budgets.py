"""Budgets de suivi par compte (décision 010, hypothèses H1 à H3 et H6 du plan).

Les montants prévus sont saisis dans le **sens naturel** de la rubrique (300 = 300 € de dépense prévus
pour une rubrique de dépense). Sens d'une rubrique sur un compte : recette = entrée ; dépense = sortie ;
épargne = sortie, sauf sur un compte d'épargne où c'est une entrée (H6).
"""

import datetime as dt
from collections import defaultdict

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import trees
from ..db import get_session
from ..errors import conflict, invalid, not_found
from ..i18n import tr
from ..models import Account, AccountKind, Budget, BudgetLine, Category, CategoryKind, Split, Transaction
from ..money import from_cents, to_cents
from ..schedules import add_months
from ..schemas import (
    BudgetCopy,
    BudgetCreate,
    BudgetLines,
    BudgetLinesOut,
    BudgetOut,
    BudgetReport,
    BudgetUpdate,
)

router = APIRouter(prefix="/budgets", tags=["budgets"])


def _get(session: Session, budget_id: int) -> Budget:
    budget = session.get(Budget, budget_id)
    if budget is None:
        raise not_found("Budget", budget_id)
    return budget


def _months(budget: Budget) -> list[dt.date]:
    return [add_months(budget.start_month, i) for i in range(budget.months)]


def _label(month: dt.date) -> str:
    return month.strftime("%Y-%m")


def _out(budget: Budget) -> dict:
    return {
        "id": budget.id,
        "account_id": budget.account_id,
        "name": budget.name,
        "start_month": budget.start_month,
        "end_month": add_months(budget.start_month, budget.months - 1),
        "months": budget.months,
        "source_budget_id": budget.source_budget_id,
        "archived": budget.archived,
        "notes": budget.notes,
    }


def _check(session: Session, budget: Budget) -> None:
    if session.get(Account, budget.account_id) is None:
        raise not_found("Compte", budget.account_id)
    end = add_months(budget.start_month, budget.months)
    for other in session.scalars(select(Budget).where(Budget.account_id == budget.account_id, Budget.id != budget.id)):
        if other.start_month < end and budget.start_month < add_months(other.start_month, other.months):
            raise conflict(
                tr("Le budget « {name} » couvre déjà une partie de cette période sur ce compte (un seul budget par compte et par mois)", name=other.name)
            )


def direction(kind: str, account_kind: str) -> int:
    if kind == CategoryKind.INCOME:
        return 1
    if kind == CategoryKind.SAVINGS and account_kind == AccountKind.SAVINGS:
        return 1
    return -1


# --- Budgets -------------------------------------------------------------------------------------


@router.get("", response_model=list[BudgetOut], summary="Budgets, du plus récent au plus ancien")
def list_budgets(
    account_id: int | None = None,
    include_archived: bool = True,
    session: Session = Depends(get_session),
):
    stmt = select(Budget).order_by(Budget.start_month.desc(), Budget.name)
    if account_id is not None:
        stmt = stmt.where(Budget.account_id == account_id)
    if not include_archived:
        stmt = stmt.where(Budget.archived.is_(False))
    return [_out(b) for b in session.scalars(stmt)]


@router.get("/{budget_id}", response_model=BudgetOut)
def get_budget(budget_id: int, session: Session = Depends(get_session)):
    return _out(_get(session, budget_id))


@router.post("", response_model=BudgetOut, status_code=201)
def create_budget(data: BudgetCreate, session: Session = Depends(get_session)):
    budget = Budget(**data.model_dump())
    _check(session, budget)
    session.add(budget)
    session.commit()
    return _out(budget)


@router.patch(
    "/{budget_id}",
    response_model=BudgetOut,
    summary="Modification partielle ; les montants des mois sortis de la période sont supprimés",
)
def update_budget(budget_id: int, data: BudgetUpdate, session: Session = Depends(get_session)):
    budget = _get(session, budget_id)
    values = data.model_dump(exclude_unset=True)
    for key, value in values.items():
        if value is None and key != "notes":
            raise invalid(tr("`{key}` ne peut pas être vide", key=key))
        setattr(budget, key, value)
    _check(session, budget)
    months = set(_months(budget))
    for line in list(budget.lines):
        if line.month not in months:
            budget.lines.remove(line)
    session.commit()
    return _out(budget)


@router.delete("/{budget_id}", status_code=204)
def delete_budget(budget_id: int, session: Session = Depends(get_session)):
    session.delete(_get(session, budget_id))
    session.commit()
    return Response(status_code=204)


@router.post(
    "/{budget_id}/copy",
    response_model=BudgetOut,
    status_code=201,
    summary="Nouveau budget à partir d'un autre : mêmes montants, mois par mois (1er mois → 1er mois…)",
)
def copy_budget(budget_id: int, data: BudgetCopy, session: Session = Depends(get_session)):
    source = _get(session, budget_id)
    copy = Budget(
        account_id=data.account_id or source.account_id,
        name=data.name,
        start_month=data.start_month or add_months(source.start_month, source.months),
        months=data.months or source.months,
        source_budget_id=source.id,
        notes=source.notes,
    )
    _check(session, copy)
    offsets = {m: i for i, m in enumerate(_months(source))}
    for line in source.lines:
        index = offsets[line.month]
        if index < copy.months:
            copy.lines.append(BudgetLine(category_id=line.category_id, month=add_months(copy.start_month, index), amount=line.amount))
    session.add(copy)
    session.commit()
    return _out(copy)


# --- Grille --------------------------------------------------------------------------------------


def _lines_out(budget: Budget) -> dict:
    lines = sorted(budget.lines, key=lambda line: (line.category_id, line.month))
    return {
        "budget_id": budget.id,
        "months": [_label(m) for m in _months(budget)],
        "lines": [{"category_id": x.category_id, "month": _label(x.month), "amount": from_cents(x.amount)} for x in lines],
    }


@router.get("/{budget_id}/lines", response_model=BudgetLinesOut, summary="Montants prévus (cellules non nulles)")
def get_lines(budget_id: int, session: Session = Depends(get_session)):
    return _lines_out(_get(session, budget_id))


@router.put(
    "/{budget_id}/lines",
    response_model=BudgetLinesOut,
    summary="Remplace toute la grille rubriques × mois ; une cellule absente ou à 0 est vide",
)
def set_lines(budget_id: int, data: BudgetLines, session: Session = Depends(get_session)):
    budget = _get(session, budget_id)
    months = set(_months(budget))
    categories = trees.load(session, Category)
    cells = {}
    for line in data.lines:
        if line.category_id not in categories:
            raise not_found("Rubrique", line.category_id)
        if line.month not in months:
            raise invalid(tr("{month} est hors de la période du budget", month=_label(line.month)))
        key = (line.category_id, line.month)
        if key in cells:
            raise invalid(tr("Cellule en double : rubrique {category}, {month}", category=line.category_id, month=_label(line.month)))
        cells[key] = to_cents(line.amount)
    existing = {(x.category_id, x.month): x for x in budget.lines}
    for key, line in existing.items():
        if not cells.get(key):
            budget.lines.remove(line)
    for (category_id, month), amount in cells.items():
        if not amount:
            continue
        if (category_id, month) in existing:
            existing[(category_id, month)].amount = amount
        else:
            budget.lines.append(BudgetLine(category_id=category_id, month=month, amount=amount))
    session.commit()
    return _lines_out(budget)


# --- Rapport -------------------------------------------------------------------------------------


@router.get(
    "/{budget_id}/report",
    response_model=BudgetReport,
    summary="Prévu / réalisé / écart par rubrique (sous-rubriques comprises) et par mois",
)
def report(budget_id: int, session: Session = Depends(get_session)):
    budget = _get(session, budget_id)
    account = session.get(Account, budget.account_id)
    months = _months(budget)
    index = {_label(m): i for i, m in enumerate(months)}
    n = len(months)
    end = add_months(budget.start_month, budget.months)

    own_planned: dict[int, list[int]] = defaultdict(lambda: [0] * n)
    for line in budget.lines:
        own_planned[line.category_id][index[_label(line.month)]] += line.amount

    # Réalisé signé, rubrique par rubrique (sans les sous-rubriques), puis cumulé par sous-arbre.
    month_of = func.strftime("%Y-%m", Transaction.date)
    stmt = (
        select(Split.category_id, month_of, func.sum(Split.amount))
        .join(Transaction, Split.transaction_id == Transaction.id)
        .where(
            Transaction.account_id == budget.account_id,
            Transaction.date >= budget.start_month,
            Transaction.date < end,
        )
        .group_by(Split.category_id, month_of)
    )
    own_actual: dict[int | None, list[int]] = defaultdict(lambda: [0] * n)
    for category_id, month, total in session.execute(stmt):
        own_actual[category_id][index[month]] += total

    nodes = trees.load(session, Category)
    ordered = trees.ordered(nodes)
    children = defaultdict(list)
    for node in nodes.values():
        children[node.parent_id].append(node.id)

    planned_tree: dict[int, list[int]] = {}
    actual_tree: dict[int, list[int]] = {}

    def cumulate(node_id: int) -> None:
        planned = list(own_planned[node_id]) if node_id in own_planned else [0] * n
        actual = list(own_actual[node_id]) if node_id in own_actual else [0] * n
        for child in children.get(node_id, []):
            cumulate(child)
            planned = [a + b for a, b in zip(planned, planned_tree[child], strict=True)]
            actual = [a + b for a, b in zip(actual, actual_tree[child], strict=True)]
        planned_tree[node_id], actual_tree[node_id] = planned, actual

    for root in children.get(None, []):
        cumulate(root)

    rows = []
    totals = {k: {"planned": [0] * n, "actual": [0] * n} for k in ("income", "expense", "savings")}
    net_planned, net_actual = [0] * n, list(own_actual[None]) if None in own_actual else [0] * n
    for node, path, depth in ordered:
        sign = direction(node.kind, account.kind)
        planned = planned_tree[node.id]
        actual = [sign * a for a in actual_tree[node.id]]
        # Écart positif = favorable : dépense moindre, recette ou épargne plus forte.
        if node.kind == CategoryKind.EXPENSE:
            variance = [p - a for p, a in zip(planned, actual, strict=True)]
        else:
            variance = [a - p for p, a in zip(planned, actual, strict=True)]
        rows.append(
            {
                "category_id": node.id,
                "parent_id": node.parent_id,
                "name": node.name,
                "path": path,
                "depth": depth,
                "kind": node.kind,
                "active": node.active,
                "has_children": bool(children.get(node.id)),
                "direction": sign,
                "own_planned": [from_cents(x) for x in (own_planned[node.id] if node.id in own_planned else [0] * n)],
                "planned": [from_cents(x) for x in planned],
                "actual": [from_cents(x) for x in actual],
                "variance": [from_cents(x) for x in variance],
                "planned_total": from_cents(sum(planned)),
                "actual_total": from_cents(sum(actual)),
                "variance_total": from_cents(sum(variance)),
            }
        )
        if node.parent_id is None:
            bucket = totals[node.kind]
            for i in range(n):
                bucket["planned"][i] += planned[i]
                bucket["actual"][i] += actual[i]
                net_planned[i] += sign * planned[i]
                net_actual[i] += sign * actual[i]

    totals["net"] = {"planned": net_planned, "actual": net_actual}
    return {
        "budget": _out(budget),
        "months": list(index),
        "rows": rows,
        "totals": {
            k: {
                "planned": [from_cents(x) for x in v["planned"]],
                "actual": [from_cents(x) for x in v["actual"]],
                "planned_total": from_cents(sum(v["planned"])),
                "actual_total": from_cents(sum(v["actual"])),
            }
            for k, v in totals.items()
        },
        "uncategorized": [from_cents(x) for x in (own_actual[None] if None in own_actual else [0] * n)],
    }
