import asyncio
import datetime as dt
import logging

from sqlalchemy.orm import Session, sessionmaker

from .backup import backup_path, run_backup
from .config import Settings
from .schedules import generate_due

log = logging.getLogger(__name__)


def seconds_until(hour: int, now: dt.datetime) -> float:
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += dt.timedelta(days=1)
    return (target - now).total_seconds()


def run_schedules(make_session: sessionmaker[Session]) -> None:
    with make_session() as session:
        generate_due(session)


async def daily_loop(settings: Settings, make_session: sessionmaker[Session]) -> None:
    """Tâches quotidiennes dans le processus (un seul processus uvicorn : pas de double exécution)."""
    while True:
        try:
            await asyncio.to_thread(run_schedules, make_session)
        except Exception:
            log.exception("Échec de la génération des échéances")
        try:
            if not backup_path(settings, dt.date.today()).exists():
                await asyncio.to_thread(run_backup, settings)
        except Exception:
            log.exception("Échec de la sauvegarde quotidienne")
        await asyncio.sleep(seconds_until(settings.backup_hour, dt.datetime.now()))
