import datetime as dt
import hashlib
import secrets

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .i18n import tr
from .models import ApiToken

_bearer = HTTPBearer(auto_error=False, description="Jeton créé avec `my-bank token create <nom>`")
_TOUCH_EVERY = dt.timedelta(hours=1)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    return "mb_" + secrets.token_urlsafe(32)


def create_token(session: Session, name: str) -> str:
    token = new_token()
    session.add(ApiToken(name=name, token_hash=hash_token(token)))
    session.commit()
    return token


def ensure_token(session: Session, name: str, token: str) -> None:
    """Le jeton `name` vaut `token` : créé s'il manque, remplacé s'il a changé."""
    row = session.scalar(select(ApiToken).where(ApiToken.name == name))
    if row is None:
        session.add(ApiToken(name=name, token_hash=hash_token(token)))
    elif row.token_hash != hash_token(token):
        row.token_hash = hash_token(token)
    else:
        return
    session.commit()


def require_token(
    request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)
) -> None:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(401, tr("jeton manquant"), headers={"WWW-Authenticate": "Bearer"})
    with request.app.state.sessionmaker() as session:
        token = session.scalar(select(ApiToken).where(ApiToken.token_hash == hash_token(credentials.credentials)))
        if token is None:
            raise HTTPException(401, tr("jeton invalide"), headers={"WWW-Authenticate": "Bearer"})
        now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
        # Écriture espacée : évite une écriture en base à chaque lecture de l'API.
        if token.last_used_at is None or now - token.last_used_at > _TOUCH_EVERY:
            token.last_used_at = now
            session.commit()
