from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .errors import conflict
from .models import Account, Attachment, Transaction
from .money import from_cents


def transaction_counts(session: Session, column, ident: int | None = None) -> dict[int, int]:
    """Nombre d'opérations par valeur de `column` (Transaction.account_id, Transaction.payee_id) ; une seule si `ident`."""
    stmt = select(column, func.count()).where(column.is_not(None)).group_by(column)
    if ident is not None:
        stmt = stmt.where(column == ident)
    return dict(session.execute(stmt).all())


def account_out(account: Account, balances: dict, transaction_count: int = 0) -> dict:
    return {
        "id": account.id,
        "name": account.name,
        "kind": account.kind,
        "number": account.number,
        "iban": account.iban,
        "currency": account.currency,
        "opening_balance": from_cents(account.opening_balance),
        "opening_date": account.opening_date,
        "closed": account.closed,
        "position": account.position,
        "notes": account.notes,
        "is_default": account.is_default,
        "balances": balances,
        "transaction_count": transaction_count,
    }


def attachment_out(att: Attachment | None) -> dict | None:
    if att is None:
        return None
    return {
        "kind": att.kind,
        "url": att.url,
        "filename": att.filename,
        "mime_type": att.mime_type,
        "size": att.size,
    }


def tx_out(tx: Transaction) -> dict:
    return {
        "id": tx.id,
        "account_id": tx.account_id,
        "date": tx.date,
        "amount": from_cents(tx.amount),
        "payee_id": tx.payee_id,
        "payment_method_id": tx.payment_method_id,
        "number": tx.number,
        "label": tx.label,
        "notes": tx.notes,
        "state": tx.state,
        "transfer_peer_id": tx.transfer_peer_id,
        "transfer_account_id": tx.transfer_peer.account_id if tx.transfer_peer else None,
        "attachment": attachment_out(tx.attachment),
        "splits": [
            {
                "id": s.id,
                "amount": from_cents(s.amount),
                "category_id": s.category_id,
                "class_id": s.class_id,
                "label": s.label,
            }
            for s in tx.splits
        ],
    }


def commit_or_conflict(session: Session, message: str) -> None:
    try:
        session.commit()
    except IntegrityError as err:
        session.rollback()
        raise conflict(message) from err
