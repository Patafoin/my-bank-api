"""Profils (décision 028) : chaque compte, rubrique, classe et tiers appartient à un profil.

Une opération, une planification ou un budget n'en a pas : il suit celui de son compte. Les références
d'un objet (tiers, rubrique, classe d'une opération ; compte d'un virement…) doivent être du même profil,
contrôlé ici. Les modes de règlement et les comptes partagés restent communs à tous les profils.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from .errors import invalid, not_found
from .i18n import tr
from .models import Account, AnalysisClass, Category, Payee, Profile

LABELS = {Account: "Compte", Category: "Rubrique", AnalysisClass: "Classe", Payee: "Tiers"}


def default_id(session: Session) -> int:
    """Premier profil (ordre, puis identifiant) : celui des créations qui n'en précisent pas."""
    return session.scalar(select(Profile.id).order_by(Profile.position, Profile.id).limit(1))


def resolve(session: Session, profile_id: int | None) -> int:
    if profile_id is None:
        return default_id(session)
    if session.get(Profile, profile_id) is None:
        raise not_found("Profil", profile_id)
    return profile_id


def check_refs(session: Session, profile_id: int, refs: list[tuple[type, int | None]]) -> None:
    """Chaque (modèle, identifiant) existe et appartient à `profile_id` ; identifiant None ignoré."""
    for model, ident in refs:
        if ident is None:
            continue
        obj = session.get(model, ident)
        if obj is None:
            raise not_found(LABELS[model], ident)
        if obj.profile_id != profile_id:
            raise invalid(
                tr("{what} « {name} » appartient à un autre profil", what=tr(LABELS[model]), name=obj.name)
            )


def account_profile(session: Session, account_id: int) -> int:
    account = session.get(Account, account_id)
    if account is None:
        raise not_found("Compte", account_id)
    return account.profile_id
