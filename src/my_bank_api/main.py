import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request

from . import __version__
from .auth import require_token
from .bootstrap import apply_env_token, run_addon_bootstrap
from .config import Settings
from .db import make_engine, make_sessionmaker
from .i18n import language_from_header, reset_language, set_language
from .jobs import daily_loop
from .migrate import upgrade
from .routers import accounts, budgets, health, imports, moves, references, schedules, shares, transactions, trees

API_PREFIX = "/api/v1"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        engine = make_engine(settings.db_url)
        upgrade(engine)
        app.state.settings = settings
        app.state.engine = engine
        app.state.sessionmaker = make_sessionmaker(engine)
        apply_env_token(settings, app.state.sessionmaker)
        # En arrière-plan : l'API répond pendant que l'add-on s'annonce à Home Assistant.
        addon = asyncio.create_task(asyncio.to_thread(run_addon_bootstrap, settings, app.state.sessionmaker))
        task = asyncio.create_task(daily_loop(settings, app.state.sessionmaker)) if settings.jobs_enabled else None
        try:
            yield
        finally:
            addon.cancel()
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            engine.dispose()

    app = FastAPI(
        title="My Bank API",
        version=__version__,
        description="Gestion de comptes bancaires personnels, sur le modèle de MS Comptes Bancaires. "
        "Montants en chaînes décimales (`\"-14.80\"`) ; état d'une opération : 0 non pointée, 1 pointée, 2 rapprochée.",
        lifespan=lifespan,
    )
    @app.middleware("http")
    async def language(request: Request, call_next):
        # messages d'erreur dans la langue du client (l'intégration HA envoie celle de Home Assistant)
        token = set_language(language_from_header(request.headers.get("accept-language")))
        try:
            return await call_next(request)
        finally:
            reset_language(token)

    app.include_router(health.router, prefix=API_PREFIX)
    protected = [Depends(require_token)]
    for router in (
        accounts.router,
        transactions.router,
        transactions.transfers,
        trees.categories,
        trees.classes,
        references.payees,
        references.payment_methods,
        schedules.router,
        budgets.router,
        shares.router,
        moves.router,
        imports.router,
    ):
        app.include_router(router, prefix=API_PREFIX, dependencies=protected)
    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
