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
    with engine.connect() as connection:
        # Clés étrangères coupées le temps des migrations : une table recréée (batch_alter_table) serait
        # sinon refusée par celles qui la référencent. Procédure recommandée par SQLite ; vérifiées à la fin.
        # Le PRAGMA est sans effet dans une transaction : il passe avant, et la connexion le retrouve à ON.
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        try:
            with connection.begin():
                cfg.attributes["connection"] = connection
                command.upgrade(cfg, "head")
                broken = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
                if broken:
                    raise RuntimeError(f"Migration : clés étrangères rompues {broken[:5]}")
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()
