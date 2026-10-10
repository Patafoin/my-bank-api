import datetime as dt
import logging
import sqlite3
from pathlib import Path

from .config import Settings

log = logging.getLogger(__name__)
PREFIX = "my_bank-"


def backup_path(settings: Settings, day: dt.date) -> Path:
    return settings.backup_dir / f"{PREFIX}{day.isoformat()}.sqlite"


def run_backup(settings: Settings, day: dt.date | None = None) -> Path:
    """Instantané cohérent via l'API de sauvegarde SQLite (une copie de fichier brute peut être incohérente)."""
    day = day or dt.date.today()
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_path(settings, day)
    tmp = target.with_suffix(".tmp")
    src = sqlite3.connect(settings.db_path)
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    tmp.replace(target)
    removed = rotate(settings)
    log.info("Sauvegarde %s (%d ancienne(s) supprimée(s))", target.name, len(removed))
    return target


def _day(path: Path) -> dt.date | None:
    try:
        return dt.date.fromisoformat(path.stem.removeprefix(PREFIX))
    except ValueError:
        return None


def rotate(settings: Settings) -> list[Path]:
    """Garde les N derniers jours et, pour les M derniers mois, la première sauvegarde du mois."""
    backups = sorted((d, p) for p in settings.backup_dir.glob(f"{PREFIX}*.sqlite") if (d := _day(p)))
    keep = {p for _, p in backups[-settings.backup_keep_daily :]} if settings.backup_keep_daily else set()
    first_of_month: dict[tuple[int, int], Path] = {}
    for day, path in backups:
        first_of_month.setdefault((day.year, day.month), path)
    for month in sorted(first_of_month)[-settings.backup_keep_monthly :] if settings.backup_keep_monthly else []:
        keep.add(first_of_month[month])
    removed = [p for _, p in backups if p not in keep]
    for path in removed:
        path.unlink()
    return removed
