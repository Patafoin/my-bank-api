"""Création d'opérations partagée entre les routes et la génération des échéances."""

import datetime as dt

from sqlalchemy.orm import Session

from .models import Split, Transaction


def make_transfer(
    session: Session,
    *,
    from_account_id: int,
    to_account_id: int,
    date: dt.date,
    cents: int,
    payment_method_id: int | None = None,
    label: str | None = None,
    notes: str | None = None,
    category_id: int | None = None,
    class_id: int | None = None,
    from_state: int = 0,
    to_state: int = 0,
) -> tuple[Transaction, Transaction]:
    """Deux opérations jumelles (débit du compte source, crédit du compte cible), déjà liées et en base (flush)."""
    legs = []
    for account_id, amount, state in ((from_account_id, -cents, from_state), (to_account_id, cents, to_state)):
        legs.append(
            Transaction(
                account_id=account_id,
                date=date,
                amount=amount,
                payment_method_id=payment_method_id,
                label=label,
                notes=notes,
                state=int(state),
                splits=[Split(amount=amount, category_id=category_id, class_id=class_id, position=0)],
            )
        )
    source, destination = legs
    session.add_all(legs)
    session.flush()
    source.transfer_peer_id, destination.transfer_peer_id = destination.id, source.id
    return source, destination
