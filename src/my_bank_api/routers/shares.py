"""Comptes partagés (décision 019) : participants, dépenses, prochain payeur et remboursements.

Totalement indépendants des comptes bancaires. Un compte clôturé est figé : ses dépenses et ses
participants ne se modifient plus (409) tant qu'il n'est pas rouvert ; son nom et ses notes, si.
"""

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import shares
from ..db import get_session
from ..errors import conflict, invalid, not_found
from ..i18n import tr
from ..models import SharedAccount, SharedExpense, SharedParticipant
from ..money import from_cents, to_cents
from ..schedules import today
from ..schemas import (
    SharedAccountCreate,
    SharedAccountDetail,
    SharedAccountOut,
    SharedAccountUpdate,
    SharedExpenseCreate,
    SharedExpenseOut,
    SharedExpenseUpdate,
    SharedParticipantIn,
    SharedParticipantOut,
)

router = APIRouter(prefix="/shared-accounts", tags=["comptes partagés"])


def _get(session: Session, account_id: int) -> SharedAccount:
    account = session.get(SharedAccount, account_id)
    if account is None:
        raise not_found("Compte partagé", account_id)
    return account


def _open(session: Session, account_id: int) -> SharedAccount:
    account = _get(session, account_id)
    if account.closed_on is not None:
        raise conflict(tr("Le compte partagé « {name} » est clôturé : le rouvrir pour le modifier", name=account.name))
    return account


def _participant(account: SharedAccount, participant_id: int) -> SharedParticipant:
    for p in account.participants:
        if p.id == participant_id:
            return p
    raise not_found("Participant", participant_id)


def _expense(account: SharedAccount, expense_id: int) -> SharedExpense:
    for e in account.expenses:
        if e.id == expense_id:
            return e
    raise not_found("Dépense", expense_id)


def _check_name(account: SharedAccount, name: str, participant_id: int | None = None) -> None:
    if any(p.name.casefold() == name.casefold() and p.id != participant_id for p in account.participants):
        raise conflict(tr("« {name} » participe déjà à ce compte partagé", name=name))


def _out(account: SharedAccount, detail: bool = False) -> dict:
    paid: dict[int, int] = {p.id: 0 for p in account.participants}
    count: dict[int, int] = {p.id: 0 for p in account.participants}
    for e in account.expenses:
        paid[e.participant_id] += e.amount
        count[e.participant_id] += 1
    people = [shares.Person(p.id, p.name, paid[p.id]) for p in account.participants]
    share = shares.shares(people)
    names = {p.id: p.name for p in account.participants}
    out = {
        "id": account.id,
        "name": account.name,
        "notes": account.notes,
        "closed": account.closed_on is not None,
        "closed_on": account.closed_on,
        "created_on": account.created_at.date(),
        "total": from_cents(sum(paid.values())),
        "expense_count": len(account.expenses),
        "participants": [
            SharedParticipantOut(
                id=p.id, name=p.name, paid=from_cents(p.paid), share=from_cents(share[p.id]),
                balance=from_cents(p.paid - share[p.id]), expense_count=count[p.id],
            )
            for p in people
        ],
        "next_participant_id": None if account.closed_on else shares.next_payer(people),
        "settlement": [
            {
                "from_participant_id": t.from_id, "from_name": names[t.from_id],
                "to_participant_id": t.to_id, "to_name": names[t.to_id], "amount": from_cents(t.amount),
            }
            for t in shares.settlement(people)
        ],
    }
    if detail:
        out["expenses"] = [
            SharedExpenseOut(id=e.id, participant_id=e.participant_id, date=e.date, amount=from_cents(e.amount), label=e.label)
            for e in account.expenses
        ]
    return out


@router.get("", response_model=list[SharedAccountOut], summary="Comptes partagés : en cours d'abord, puis clôturés, les plus récents en premier")
def list_shared_accounts(session: Session = Depends(get_session)):
    accounts = session.scalars(select(SharedAccount)).all()
    accounts = sorted(accounts, key=lambda a: (a.closed_on is not None, -(a.closed_on or a.created_at.date()).toordinal(), -a.id))
    return [_out(a) for a in accounts]


@router.get("/{account_id}", response_model=SharedAccountDetail)
def get_shared_account(account_id: int, session: Session = Depends(get_session)):
    return _out(_get(session, account_id), detail=True)


@router.post("", response_model=SharedAccountDetail, status_code=201)
def create_shared_account(data: SharedAccountCreate, session: Session = Depends(get_session)):
    folded = [n.casefold() for n in data.participants]
    if len(set(folded)) != len(folded):
        raise invalid(tr("Deux participants portent le même nom"))
    account = SharedAccount(name=data.name, notes=data.notes)
    account.participants = [SharedParticipant(name=n, position=i) for i, n in enumerate(data.participants)]
    session.add(account)
    session.commit()
    session.refresh(account)
    return _out(account, detail=True)


@router.patch("/{account_id}", response_model=SharedAccountDetail, summary="Nom, notes, clôture (`closed: true`) ou réouverture")
def update_shared_account(account_id: int, data: SharedAccountUpdate, session: Session = Depends(get_session)):
    account = _get(session, account_id)
    values = data.model_dump(exclude_unset=True)
    if "name" in values:
        if values["name"] is None:
            raise invalid(tr("`{key}` ne peut pas être vide", key="name"))
        account.name = values["name"]
    if "notes" in values:
        account.notes = values["notes"]
    if values.get("closed") is not None:
        if values["closed"] and account.closed_on is None:
            account.closed_on = today()
        elif not values["closed"]:
            account.closed_on = None
    session.commit()
    return _out(account, detail=True)


@router.delete("/{account_id}", status_code=204, summary="Supprime le compte partagé, ses participants et ses dépenses")
def delete_shared_account(account_id: int, session: Session = Depends(get_session)):
    session.delete(_get(session, account_id))
    session.commit()
    return Response(status_code=204)


@router.post("/{account_id}/participants", response_model=SharedAccountDetail, status_code=201)
def add_participant(account_id: int, data: SharedParticipantIn, session: Session = Depends(get_session)):
    account = _open(session, account_id)
    _check_name(account, data.name)
    position = max((p.position for p in account.participants), default=-1) + 1
    account.participants.append(SharedParticipant(name=data.name, position=position))
    session.commit()
    session.refresh(account)
    return _out(account, detail=True)


@router.patch("/{account_id}/participants/{participant_id}", response_model=SharedAccountDetail, summary="Renommer un participant")
def rename_participant(account_id: int, participant_id: int, data: SharedParticipantIn, session: Session = Depends(get_session)):
    account = _open(session, account_id)
    participant = _participant(account, participant_id)
    _check_name(account, data.name, participant_id)
    participant.name = data.name
    session.commit()
    return _out(account, detail=True)


@router.delete(
    "/{account_id}/participants/{participant_id}",
    response_model=SharedAccountDetail,
    summary="Retirer un participant qui n'a payé aucune dépense (il en reste au moins deux)",
)
def remove_participant(account_id: int, participant_id: int, session: Session = Depends(get_session)):
    account = _open(session, account_id)
    participant = _participant(account, participant_id)
    if any(e.participant_id == participant_id for e in account.expenses):
        raise conflict(tr("{name} a déjà payé des dépenses : les supprimer ou les attribuer à quelqu'un d'autre avant de le retirer", name=participant.name))
    if len(account.participants) <= 2:
        raise conflict(tr("Un compte partagé garde au moins deux participants"))
    account.participants.remove(participant)
    session.commit()
    return _out(account, detail=True)


@router.post("/{account_id}/expenses", response_model=SharedAccountDetail, status_code=201)
def add_expense(account_id: int, data: SharedExpenseCreate, session: Session = Depends(get_session)):
    account = _open(session, account_id)
    _participant(account, data.participant_id)
    if data.amount <= 0:
        raise invalid(tr("Le montant d'une dépense doit être positif"))
    account.expenses.append(
        SharedExpense(participant_id=data.participant_id, amount=to_cents(data.amount), date=data.date or today(), label=data.label)
    )
    session.commit()
    session.refresh(account)
    return _out(account, detail=True)


@router.patch("/{account_id}/expenses/{expense_id}", response_model=SharedAccountDetail)
def update_expense(account_id: int, expense_id: int, data: SharedExpenseUpdate, session: Session = Depends(get_session)):
    account = _open(session, account_id)
    expense = _expense(account, expense_id)
    values = {key: getattr(data, key) for key in data.model_fields_set}  # Money reste un Decimal
    for key in ("participant_id", "amount", "date"):
        if key in values and values[key] is None:
            raise invalid(tr("`{key}` ne peut pas être vide", key=key))
    if "participant_id" in values:
        _participant(account, values["participant_id"])
        expense.participant_id = values["participant_id"]
    if "amount" in values:
        if values["amount"] <= 0:
            raise invalid(tr("Le montant d'une dépense doit être positif"))
        expense.amount = to_cents(values["amount"])
    if "date" in values:
        expense.date = values["date"]
    if "label" in values:
        expense.label = values["label"]
    session.commit()
    session.refresh(account)
    return _out(account, detail=True)


@router.delete("/{account_id}/expenses/{expense_id}", response_model=SharedAccountDetail)
def delete_expense(account_id: int, expense_id: int, session: Session = Depends(get_session)):
    account = _open(session, account_id)
    account.expenses.remove(_expense(account, expense_id))
    session.commit()
    session.refresh(account)
    return _out(account, detail=True)
