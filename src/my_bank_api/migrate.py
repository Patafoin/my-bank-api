from alembic import command
from alembic.config import Config
from sqlalchemy import Engine


def alembic_config(db_url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", "my_bank_api:migrations")
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def upgrade(engine: Engine) -> None:
    cfg = alembic_config(str(engine.url))
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        command.upgrade(cfg, "head")
