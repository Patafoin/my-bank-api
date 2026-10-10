from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..db import get_session
from ..errors import conflict, invalid, not_found
from ..i18n import tr
from ..models import Category, Payee, PaymentMethod, Schedule, Transaction
from ..schemas import (
    PayeeCreate,
    PayeeOut,
    PayeeUpdate,
    PaymentMethodCreate,
    PaymentMethodOut,
    PaymentMethodUpdate,
)
from ..serialize import commit_or_conflict, transaction_counts

payment_methods = APIRouter(prefix="/payment-methods", tags=["modes de règlement"])
payees = APIRouter(prefix="/payees", tags=["tiers"])


# --- Modes de règlement --------------------------------------------------------------------------


def _get_method(session: Session, method_id: int) -> PaymentMethod:
    method = session.get(PaymentMethod, method_id)
    if method is None:
        raise not_found("Mode de règlement", method_id)
    return method


@payment_methods.get("", response_model=list[PaymentMethodOut])
def list_payment_methods(session: Session = Depends(get_session)):
    return session.scalars(select(PaymentMethod).order_by(PaymentMethod.position, PaymentMethod.name)).all()


@payment_methods.post("", response_model=PaymentMethodOut, status_code=201)
def create_payment_method(data: PaymentMethodCreate, session: Session = Depends(get_session)):
    method = PaymentMethod(**data.model_dump())
    session.add(method)
    commit_or_conflict(session, tr("Le mode de règlement « {name} » existe déjà", name=data.name))
    return method


@payment_methods.patch("/{method_id}", response_model=PaymentMethodOut)
def update_payment_method(method_id: int, data: PaymentMethodUpdate, session: Session = Depends(get_session)):
    method = _get_method(session, method_id)
    for key, value in data.model_dump(exclude_unset=True).items():
        if value is None:
            raise invalid(tr("`{key}` ne peut pas être vide", key=key))
        setattr(method, key, value)
    commit_or_conflict(session, tr("Le mode de règlement « {name} » existe déjà", name=method.name))
    return method


@payment_methods.delete("/{method_id}", status_code=204)
def delete_payment_method(method_id: int, session: Session = Depends(get_session)):
    method = _get_method(session, method_id)
    used = session.scalar(select(func.count()).where(Transaction.payment_method_id == method_id))
    if used:
        raise conflict(tr("« {name} » est utilisé par {used} opération(s) : le désactiver plutôt", name=method.name, used=used))
    if session.scalar(select(func.count()).where(Schedule.payment_method_id == method_id)):
        raise conflict(tr("« {name} » est utilisé par une opération planifiée : le désactiver plutôt", name=method.name))
    session.delete(method)
    session.commit()
    return Response(status_code=204)


# --- Tiers ---------------------------------------------------------------------------------------


def _get_payee(session: Session, payee_id: int) -> Payee:
    payee = session.get(Payee, payee_id)
    if payee is None:
        raise not_found("Tiers", payee_id)
    return payee


def _check_category(session: Session, category_id: int | None) -> None:
    if category_id is not None and session.get(Category, category_id) is None:
        raise not_found("Rubrique", category_id)


def _payee_out(session: Session, payee: Payee, counts: dict[int, int] | None = None) -> PayeeOut:
    if counts is None:
        counts = transaction_counts(session, Transaction.payee_id, payee.id)
    return PayeeOut.model_validate(payee).model_copy(update={"transaction_count": counts.get(payee.id, 0)})


@payees.get("", response_model=list[PayeeOut], summary="Tiers, triés par nom ; `q` filtre sur le nom")
def list_payees(
    q: str | None = Query(None, description="Texte contenu dans le nom ou le nom abrégé"),
    active: bool | None = None,
    session: Session = Depends(get_session),
):
    stmt = select(Payee).order_by(func.lower(Payee.name))
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(or_(Payee.name.ilike(pattern), Payee.short_name.ilike(pattern)))
    if active is not None:
        stmt = stmt.where(Payee.active.is_(active))
    counts = transaction_counts(session, Transaction.payee_id)
    return [_payee_out(session, p, counts) for p in session.scalars(stmt)]


@payees.get("/{payee_id}", response_model=PayeeOut)
def get_payee(payee_id: int, session: Session = Depends(get_session)):
    return _payee_out(session, _get_payee(session, payee_id))


@payees.post("", response_model=PayeeOut, status_code=201)
def create_payee(data: PayeeCreate, session: Session = Depends(get_session)):
    _check_category(session, data.default_category_id)
    payee = Payee(**data.model_dump())
    session.add(payee)
    commit_or_conflict(session, tr("Le tiers « {name} » existe déjà", name=data.name))
    return _payee_out(session, payee)


@payees.patch("/{payee_id}", response_model=PayeeOut)
def update_payee(payee_id: int, data: PayeeUpdate, session: Session = Depends(get_session)):
    payee = _get_payee(session, payee_id)
    values = data.model_dump(exclude_unset=True)
    for required in ("name", "active"):
        if required in values and values[required] is None:
            raise invalid(tr("`{key}` ne peut pas être vide", key=required))
    if "default_category_id" in values:
        _check_category(session, values["default_category_id"])
    for key, value in values.items():
        setattr(payee, key, value)
    commit_or_conflict(session, tr("Le tiers « {name} » existe déjà", name=payee.name))
    return _payee_out(session, payee)


@payees.delete("/{payee_id}", status_code=204)
def delete_payee(payee_id: int, session: Session = Depends(get_session)):
    payee = _get_payee(session, payee_id)
    used = session.scalar(select(func.count()).where(Transaction.payee_id == payee_id))
    if used:
        raise conflict(tr("« {name} » est utilisé par {used} opération(s) : le désactiver plutôt", name=payee.name, used=used))
    if session.scalar(select(func.count()).where(Schedule.payee_id == payee_id)):
        raise conflict(tr("« {name} » est utilisé par une opération planifiée : le désactiver plutôt", name=payee.name))
    session.delete(payee)
    session.commit()
    return Response(status_code=204)
