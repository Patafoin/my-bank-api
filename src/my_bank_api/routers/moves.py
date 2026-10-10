"""Déplacement en masse des opérations d'un élément vers un autre (décision 020).

Rubrique, classe, tiers : toutes les opérations sont déplacées, rapprochées comprises — aucun solde ne
change. Compte : les soldes des deux comptes changent ; les opérations rapprochées ne sont déplacées
qu'avec `include_reconciled`, et les virements entre les deux comptes sont refusés (ils deviendraient
des virements d'un compte vers lui-même). Les planifications qui utilisent l'élément suivent ; les
montants de budget, non. La source n'est pas supprimée : c'est au client de le demander ensuite.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session, aliased

from ..db import get_session
from ..errors import conflict, invalid, not_found
from ..i18n import tr
from ..models import Account, AnalysisClass, Category, Payee, Schedule, Split, Transaction, TxState
from ..schemas import MoveIn, MoveOut

router = APIRouter(tags=["déplacements"])


def _pair(session: Session, model, label: str, source_id: int, target_id: int):
    if source_id == target_id:
        raise invalid(tr("La cible doit être différente de la source"))
    source = session.get(model, source_id)
    if source is None:
        raise not_found(label, source_id)
    target = session.get(model, target_id)
    if target is None:
        raise not_found(label, target_id)
    return source, target


def _move_splits(session: Session, column, source_id: int, target_id: int, schedule_column) -> MoveOut:
    moved = session.scalar(select(func.count(func.distinct(Split.transaction_id))).where(column == source_id))
    session.execute(update(Split).where(column == source_id).values({column.key: target_id}))
    schedules = session.execute(
        update(Schedule).where(schedule_column == source_id).values({schedule_column.key: target_id})
    ).rowcount
    session.commit()
    return MoveOut(transactions=moved, schedules=schedules)


@router.post("/categories/{category_id}/move", response_model=MoveOut, summary="Déplacer toutes les opérations d'une rubrique vers une autre")
def move_category(category_id: int, data: MoveIn, session: Session = Depends(get_session)):
    _pair(session, Category, "Rubrique", category_id, data.target_id)
    return _move_splits(session, Split.category_id, category_id, data.target_id, Schedule.category_id)


@router.post("/classes/{class_id}/move", response_model=MoveOut, summary="Déplacer toutes les opérations d'une classe vers une autre")
def move_class(class_id: int, data: MoveIn, session: Session = Depends(get_session)):
    _pair(session, AnalysisClass, "Classe", class_id, data.target_id)
    return _move_splits(session, Split.class_id, class_id, data.target_id, Schedule.class_id)


@router.post("/payees/{payee_id}/move", response_model=MoveOut, summary="Déplacer toutes les opérations d'un tiers vers un autre")
def move_payee(payee_id: int, data: MoveIn, session: Session = Depends(get_session)):
    _pair(session, Payee, "Tiers", payee_id, data.target_id)
    moved = session.execute(update(Transaction).where(Transaction.payee_id == payee_id).values(payee_id=data.target_id)).rowcount
    schedules = session.execute(update(Schedule).where(Schedule.payee_id == payee_id).values(payee_id=data.target_id)).rowcount
    session.commit()
    return MoveOut(transactions=moved, schedules=schedules)


@router.post(
    "/accounts/{account_id}/move",
    response_model=MoveOut,
    summary="Déplacer toutes les opérations d'un compte vers un autre (rapprochées seulement avec include_reconciled)",
)
def move_account(account_id: int, data: MoveIn, session: Session = Depends(get_session)):
    source, target = _pair(session, Account, "Compte", account_id, data.target_id)
    peer = aliased(Transaction)
    between = session.scalar(
        select(func.count()).select_from(Transaction).join(peer, Transaction.transfer_peer_id == peer.id)
        .where(Transaction.account_id == account_id, peer.account_id == data.target_id)
    )
    if between:
        raise conflict(
            tr(
                "{n} virement(s) entre « {source} » et « {target} » deviendraient des virements d'un compte vers lui-même : les supprimer d'abord",
                n=between, source=source.name, target=target.name,
            )
        )
    self_schedules = session.scalar(
        select(func.count()).select_from(Schedule).where(
            or_(
                (Schedule.account_id == account_id) & (Schedule.transfer_account_id == data.target_id),
                (Schedule.transfer_account_id == account_id) & (Schedule.account_id == data.target_id),
            )
        )
    )
    if self_schedules:
        raise conflict(
            tr(
                "{n} virement(s) planifié(s) entre « {source} » et « {target} » : les supprimer ou les modifier d'abord",
                n=self_schedules, source=source.name, target=target.name,
            )
        )
    reconciled = session.scalar(
        select(func.count()).select_from(Transaction).where(Transaction.account_id == account_id, Transaction.state == TxState.RECONCILED)
    )
    if reconciled and not data.include_reconciled:
        raise conflict(
            tr(
                "« {source} » a {n} opération(s) rapprochée(s) : les déplacer changerait les soldes rapprochés des deux comptes "
                "(cocher « inclure les opérations rapprochées »)",
                source=source.name, n=reconciled,
            )
        )
    moved = session.execute(update(Transaction).where(Transaction.account_id == account_id).values(account_id=data.target_id)).rowcount
    schedules = session.execute(update(Schedule).where(Schedule.account_id == account_id).values(account_id=data.target_id)).rowcount
    schedules += session.execute(
        update(Schedule).where(Schedule.transfer_account_id == account_id).values(transfer_account_id=data.target_id)
    ).rowcount
    session.commit()
    return MoveOut(transactions=moved, schedules=schedules)
