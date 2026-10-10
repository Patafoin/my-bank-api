import datetime as dt
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..balances import account_balances, balances_by_account
from ..db import get_session
from ..errors import conflict, invalid, not_found
from ..i18n import tr
from ..models import Account, Budget, Schedule, Transaction
from ..money import from_cents, to_cents
from ..schemas import (
    AccountBalancesOut,
    AccountCreate,
    AccountOut,
    AccountTransactions,
    AccountUpdate,
)
from ..serialize import account_out, commit_or_conflict, transaction_counts, tx_out

router = APIRouter(prefix="/accounts", tags=["comptes"])


def _get(session: Session, account_id: int) -> Account:
    account = session.get(Account, account_id)
    if account is None:
        raise not_found("Compte", account_id)
    return account


@router.get("", response_model=list[AccountOut], summary="Liste des comptes avec leurs trois soldes")
def list_accounts(
    date: dt.date | None = Query(None, description="Soldes à cette date ; par défaut toutes les opérations"),
    include_closed: bool = True,
    session: Session = Depends(get_session),
):
    stmt = select(Account).order_by(Account.position, Account.name)
    if not include_closed:
        stmt = stmt.where(Account.closed.is_(False))
    balances = balances_by_account(session, date)
    counts = transaction_counts(session, Transaction.account_id)
    return [account_out(a, balances[a.id], counts.get(a.id, 0)) for a in session.scalars(stmt)]


def _out(session: Session, account: Account, date: dt.date | None = None) -> dict:
    count = transaction_counts(session, Transaction.account_id, account.id).get(account.id, 0)
    return account_out(account, account_balances(session, account, date), count)


@router.post("", response_model=AccountOut, status_code=201)
def create_account(data: AccountCreate, session: Session = Depends(get_session)):
    values = data.model_dump()
    values["opening_balance"] = to_cents(data.opening_balance)
    account = Account(**values)
    session.add(account)
    commit_or_conflict(session, tr("Le compte « {name} » existe déjà", name=data.name))
    return _out(session, account)


@router.get("/{account_id}", response_model=AccountOut)
def get_account(account_id: int, date: dt.date | None = None, session: Session = Depends(get_session)):
    account = _get(session, account_id)
    return _out(session, account, date)


@router.patch("/{account_id}", response_model=AccountOut)
def update_account(account_id: int, data: AccountUpdate, session: Session = Depends(get_session)):
    account = _get(session, account_id)
    values = data.model_dump(exclude_unset=True)
    for required in ("name", "kind", "opening_balance", "closed", "position"):
        if required in values and values[required] is None:
            raise invalid(tr("`{key}` ne peut pas être vide", key=required))
    if "opening_balance" in values:
        # model_dump() sérialise les montants en texte : repartir de l'attribut (Decimal).
        values["opening_balance"] = to_cents(data.opening_balance)
    for key, value in values.items():
        setattr(account, key, value)
    commit_or_conflict(session, tr("Le compte « {name} » existe déjà", name=account.name))
    return _out(session, account)


@router.delete("/{account_id}", status_code=204)
def delete_account(account_id: int, session: Session = Depends(get_session)):
    account = _get(session, account_id)
    count = session.scalar(select(func.count()).where(Transaction.account_id == account_id))
    if count:
        raise conflict(tr("Le compte a {count} opération(s) : le clôturer plutôt que le supprimer", count=count))
    schedules = session.scalar(
        select(func.count()).where((Schedule.account_id == account_id) | (Schedule.transfer_account_id == account_id))
    )
    if schedules or session.scalar(select(func.count()).where(Budget.account_id == account_id)):
        raise conflict(tr("Le compte a des opérations planifiées ou des budgets : les supprimer d'abord"))
    session.delete(account)
    session.commit()
    return Response(status_code=204)


@router.get("/{account_id}/balances", response_model=AccountBalancesOut, summary="Les trois soldes d'un compte")
def get_balances(account_id: int, date: dt.date | None = None, session: Session = Depends(get_session)):
    account = _get(session, account_id)
    return {"account_id": account_id, "date": date, **account_balances(session, account, date)}


@router.get(
    "/{account_id}/transactions",
    response_model=AccountTransactions,
    summary="Opérations d'un compte sur une période, avec le solde après chaque ligne",
)
def account_transactions(
    account_id: int,
    start: dt.date | None = None,
    end: dt.date | None = None,
    state: list[int] | None = Query(None, description="Filtre d'affichage ; n'influe pas sur les soldes"),
    order: Literal["asc", "desc"] = "asc",
    session: Session = Depends(get_session),
):
    account = _get(session, account_id)
    if start and end and start > end:
        raise invalid(tr("`start` doit précéder `end`"))
    tx = Transaction
    opening = account.opening_balance
    if start is not None:
        before = select(func.coalesce(func.sum(tx.amount), 0)).where(tx.account_id == account_id, tx.date < start)
        opening += session.scalar(before)
    running = func.sum(tx.amount).over(order_by=(tx.date, tx.id))
    stmt = select(tx, running).where(tx.account_id == account_id).order_by(tx.date, tx.id)
    if start is not None:
        stmt = stmt.where(tx.date >= start)
    if end is not None:
        stmt = stmt.where(tx.date <= end)
    items = []
    for transaction, cumulative in session.execute(stmt):
        if state is not None and transaction.state not in state:
            continue
        items.append({**tx_out(transaction), "balance_after": from_cents(opening + cumulative)})
    if order == "desc":
        items.reverse()
    return {"account_id": account_id, "start": start, "end": end, "opening_balance": from_cents(opening), "items": items}
