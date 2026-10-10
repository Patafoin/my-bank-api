import base64
import binascii

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy.orm import Session

from ..db import get_session
from ..errors import invalid
from ..i18n import tr
from ..ms_import import ImportFileError, QifFile, decode, run_import
from ..schemas import Strict

router = APIRouter(prefix="/import", tags=["import"])


class QifIn(Strict):
    account: str = Field(min_length=1, max_length=100, description="Nom du compte (créé s'il n'existe pas)")
    content_base64: str = Field(description="Fichier QIF brut encodé en base64 (Windows-1252 ou UTF-8)")


class MsImportIn(Strict):
    files: list[QifIn] = Field(default_factory=list)
    payees_csv_base64: str | None = Field(None, description="Export CSV des tiers de MS Comptes Bancaires, en base64")
    dry_run: bool = Field(True, description="Simulation : rien n'est enregistré, le rapport est quand même calculé")


def _b64(value: str, what: str) -> str:
    try:
        return decode(base64.b64decode(value, validate=True))
    except (binascii.Error, ValueError) as err:
        raise invalid(tr("{what} : base64 invalide", what=what)) from err


@router.post(
    "/ms",
    summary="Import des exports MS Comptes Bancaires (QIF par compte + tiers CSV)",
    description="Les comptes doivent être vides. Les virements entre comptes importés ensemble sont reconnus "
    "(même date, montants opposés, tiers = nom d'un des comptes) et reliés. Par défaut : simulation.",
)
def import_ms(data: MsImportIn, session: Session = Depends(get_session)) -> dict:
    files = [QifFile(f.account, _b64(f.content_base64, f.account)) for f in data.files]
    payees = _b64(data.payees_csv_base64, "tiers") if data.payees_csv_base64 else None
    try:
        report = run_import(session, files, payees)
    except ImportFileError as err:
        session.rollback()
        raise invalid(str(err)) from err
    if data.dry_run:
        session.rollback()
    else:
        session.commit()
    return {"dry_run": data.dry_run, **report}
