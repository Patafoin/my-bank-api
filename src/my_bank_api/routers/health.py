from fastapi import APIRouter, Request
from sqlalchemy import text

from .. import __version__

router = APIRouter(tags=["santé"])


@router.get("/health", summary="État du service (sans jeton)")
def health(request: Request) -> dict:
    with request.app.state.sessionmaker() as session:
        session.execute(text("SELECT 1"))
    return {"status": "ok", "version": __version__}
