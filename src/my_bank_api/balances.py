import datetime as dt

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from .models import Account, Transaction, TxState
from .money import from_cents


def balances_by_account(session: Session, at: dt.date | None = None) -> dict[int, dict]:
    """Trois soldes par compte, calculés à la lecture (décision 011) ; solde initial inclus dans les trois."""
    tx = Transaction
    join_on = tx.account_id == Account.id
    if at is not None:
        join_on = and_(join_on, tx.date <= at)
    pointed = func.coalesce(func.sum(case((tx.state >= TxState.POINTED, tx.amount))), 0)
    reconciled = func.coalesce(func.sum(case((tx.state == TxState.RECONCILED, tx.amount))), 0)
    total = func.coalesce(func.sum(tx.amount), 0)
    stmt = (
        select(Account.id, Account.opening_balance, total, pointed, reconciled)
        .select_from(Account)
        .outerjoin(tx, join_on)
        .group_by(Account.id)
    )
    result = {}
    for account_id, opening, t, p, r in session.execute(stmt):
        result[account_id] = {
            "total": from_cents(opening + t),
            "pointed": from_cents(opening + p),
            "reconciled": from_cents(opening + r),
        }
    return result


def account_balances(session: Session, account: Account, at: dt.date | None = None) -> dict:
    tx = Transaction
    stmt = select(
        func.coalesce(func.sum(tx.amount), 0),
        func.coalesce(func.sum(case((tx.state >= TxState.POINTED, tx.amount))), 0),
        func.coalesce(func.sum(case((tx.state == TxState.RECONCILED, tx.amount))), 0),
    ).where(tx.account_id == account.id)
    if at is not None:
        stmt = stmt.where(tx.date <= at)
    t, p, r = session.execute(stmt).one()
    opening = account.opening_balance
    return {"total": from_cents(opening + t), "pointed": from_cents(opening + p), "reconciled": from_cents(opening + r)}
