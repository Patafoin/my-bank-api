from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import profiles as prof
from ..db import get_session
from ..errors import conflict, invalid, not_found
from ..i18n import tr
from ..models import (
    Account,
    AnalysisClass,
    Attachment,
    Budget,
    BudgetLine,
    Category,
    Payee,
    Profile,
    Schedule,
    ScheduleOccurrence,
    Split,
    Transaction,
)
from ..schemas import ProfileCreate, ProfileOut, ProfileUpdate
from ..serialize import commit_or_conflict

router = APIRouter(prefix="/profiles", tags=["profils"])

COUNTED = {"accounts": Account, "categories": Category, "classes": AnalysisClass, "payees": Payee}


def _get(session: Session, profile_id: int) -> Profile:
    profile = session.get(Profile, profile_id)
    if profile is None:
        raise not_found("Profil", profile_id)
    return profile


def _counts(session: Session) -> dict[str, dict[int, int]]:
    counts = {
        key: dict(session.execute(select(model.profile_id, func.count()).group_by(model.profile_id)).all())
        for key, model in COUNTED.items()
    }
    # Opérations : par le profil de leur compte (de quoi annoncer ce qu'une suppression emporterait).
    stmt = select(Account.profile_id, func.count()).join(Transaction, Transaction.account_id == Account.id).group_by(Account.profile_id)
    counts["transactions"] = dict(session.execute(stmt).all())
    return counts


def _out(profile: Profile, default_id: int, counts: dict[str, dict[int, int]]) -> dict:
    return {
        "id": profile.id,
        "name": profile.name,
        "position": profile.position,
        "is_default": profile.id == default_id,
        **{key: counts[key].get(profile.id, 0) for key in (*COUNTED, "transactions")},
    }


def _one(session: Session, profile: Profile) -> dict:
    return _out(profile, prof.default_id(session), _counts(session))


@router.get("", response_model=list[ProfileOut], summary="Profils, dans l'ordre (le premier est le profil par défaut)")
def list_profiles(session: Session = Depends(get_session)):
    default_id, counts = prof.default_id(session), _counts(session)
    return [_out(p, default_id, counts) for p in session.scalars(select(Profile).order_by(Profile.position, Profile.id))]


@router.post("", response_model=ProfileOut, status_code=201, summary="Nouveau profil, vide")
def create_profile(data: ProfileCreate, session: Session = Depends(get_session)):
    profile = Profile(**data.model_dump())
    session.add(profile)
    commit_or_conflict(session, tr("Le profil « {name} » existe déjà", name=data.name))
    return _one(session, profile)


@router.patch("/{profile_id}", response_model=ProfileOut)
def update_profile(profile_id: int, data: ProfileUpdate, session: Session = Depends(get_session)):
    profile = _get(session, profile_id)
    for key, value in data.model_dump(exclude_unset=True).items():
        if value is None:
            raise invalid(tr("`{key}` ne peut pas être vide", key=key))
        setattr(profile, key, value)
    commit_or_conflict(session, tr("Le profil « {name} » existe déjà", name=profile.name))
    return _one(session, profile)


@router.delete(
    "/{profile_id}",
    status_code=204,
    summary="Supprime un profil (jamais le dernier) ; avec `with_data`, tout son contenu aussi, rapprochées comprises",
)
def delete_profile(
    profile_id: int,
    with_data: bool = Query(
        False, description="Supprime aussi comptes, opérations, justificatifs, planifications, budgets, rubriques, classes et tiers"
    ),
    session: Session = Depends(get_session),
):
    profile = _get(session, profile_id)
    if session.scalar(select(func.count()).select_from(Profile)) == 1:
        raise conflict(tr("Le dernier profil ne peut pas être supprimé"))
    counts = _counts(session)
    if not with_data and any(counts[key].get(profile_id) for key in COUNTED):
        raise conflict(
            tr("Le profil « {name} » contient encore des comptes, rubriques, classes ou tiers : les supprimer d'abord", name=profile.name)
        )
    if with_data:
        _delete_content(session, profile_id)
    session.delete(profile)
    try:
        session.commit()
    except IntegrityError as err:  # une donnée d'un autre profil référence encore celui-ci : rien n'est supprimé
        session.rollback()
        raise conflict(tr("Le profil « {name} » est référencé par des données d'un autre profil : suppression annulée", name=profile.name)) from err
    return Response(status_code=204)


def _delete_content(session: Session, profile_id: int) -> None:
    """Tout le contenu du profil, dans l'ordre des clés étrangères (décision 028). Validé par l'appelant."""
    accounts = select(Account.id).where(Account.profile_id == profile_id)
    txs = select(Transaction.id).where(Transaction.account_id.in_(accounts))
    schedules = select(Schedule.id).where(or_(Schedule.account_id.in_(accounts), Schedule.transfer_account_id.in_(accounts)))
    budgets = select(Budget.id).where(Budget.account_id.in_(accounts))
    categories = select(Category.id).where(Category.profile_id == profile_id)
    run = session.execute
    run(update(Transaction).where(or_(Transaction.id.in_(txs), Transaction.transfer_peer_id.in_(txs))).values(transfer_peer_id=None))
    run(delete(ScheduleOccurrence).where(ScheduleOccurrence.schedule_id.in_(schedules)))
    run(delete(Schedule).where(Schedule.id.in_(schedules)))
    run(delete(BudgetLine).where(or_(BudgetLine.budget_id.in_(budgets), BudgetLine.category_id.in_(categories))))
    run(update(Budget).where(Budget.source_budget_id.in_(budgets)).values(source_budget_id=None))
    run(delete(Budget).where(Budget.id.in_(budgets)))
    run(delete(Attachment).where(Attachment.transaction_id.in_(txs)))
    run(delete(Split).where(Split.transaction_id.in_(txs)))
    run(delete(Transaction).where(Transaction.id.in_(txs)))
    run(update(Payee).where(Payee.profile_id == profile_id).values(default_category_id=None))
    run(delete(Payee).where(Payee.profile_id == profile_id))
    for model in (Category, AnalysisClass):
        run(update(model).where(model.profile_id == profile_id).values(parent_id=None))
        run(delete(model).where(model.profile_id == profile_id))
    run(delete(Account).where(Account.profile_id == profile_id))
    session.expire_all()
