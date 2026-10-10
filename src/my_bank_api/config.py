import json
import os
from dataclasses import dataclass
from pathlib import Path


def _addon_options() -> dict:
    """Options saisies dans l'interface de l'add-on Home Assistant (absentes hors add-on)."""
    try:
        return json.loads(Path("/data/options.json").read_text())
    except (OSError, ValueError):
        return {}


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    backup_dir: Path
    backup_hour: int = 3
    backup_keep_daily: int = 14
    backup_keep_monthly: int = 12
    jobs_enabled: bool = True
    api_token: str | None = None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "my_bank.sqlite"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        # En add-on Home Assistant, seul /data est persistant (et compris dans les sauvegardes de HA).
        default_backup_dir = "/data/backups" if env.get("SUPERVISOR_TOKEN") else "/backups"
        return cls(
            data_dir=Path(env.get("MYBANK_DATA_DIR", "/data")),
            backup_dir=Path(env.get("MYBANK_BACKUP_DIR", default_backup_dir)),
            backup_hour=int(env.get("MYBANK_BACKUP_HOUR", "3")),
            backup_keep_daily=int(env.get("MYBANK_BACKUP_KEEP_DAILY", "14")),
            backup_keep_monthly=int(env.get("MYBANK_BACKUP_KEEP_MONTHLY", "12")),
            jobs_enabled=_bool(env.get("MYBANK_JOBS_ENABLED", "true")),
            api_token=env.get("MYBANK_API_TOKEN") or (_addon_options().get("api_token") if env.get("SUPERVISOR_TOKEN") else None) or None,
        )
