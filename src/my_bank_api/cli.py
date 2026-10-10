import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select

from .auth import create_token
from .backup import run_backup
from .config import Settings
from .db import make_engine, make_sessionmaker
from .migrate import upgrade
from .models import ApiToken
from .ms_import import ImportFileError, QifFile, decode, run_import


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="my-bank", description="Administration de My Bank API")
    sub = parser.add_subparsers(dest="command", required=True)
    token = sub.add_parser("token", help="Jetons d'accès à l'API").add_subparsers(dest="action", required=True)
    token.add_parser("create", help="Créer un jeton (affiché une seule fois)").add_argument("name")
    token.add_parser("list", help="Lister les jetons")
    token.add_parser("revoke", help="Révoquer un jeton").add_argument("name")
    sub.add_parser("backup", help="Faire une sauvegarde maintenant")
    sub.add_parser("migrate", help="Appliquer les migrations de schéma")
    imp = sub.add_parser("import-ms", help="Importer des exports MS Comptes Bancaires (simulation par défaut)")
    imp.add_argument("qif", nargs="*", metavar="COMPTE=FICHIER.qif")
    imp.add_argument("--payees", metavar="tiers.csv")
    imp.add_argument("--apply", action="store_true", help="Enregistrer (sinon simulation)")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.db_url)
    upgrade(engine)
    if args.command == "migrate":
        print("Schéma à jour")
        return 0
    if args.command == "backup":
        print(run_backup(settings))
        return 0
    with make_sessionmaker(engine)() as session:
        if args.command == "import-ms":
            return _import_ms(session, args)
        if args.action == "create":
            if session.scalar(select(ApiToken).where(ApiToken.name == args.name)):
                print(f"Un jeton nommé « {args.name} » existe déjà", file=sys.stderr)
                return 1
            print(create_token(session, args.name))
        elif args.action == "list":
            for t in session.scalars(select(ApiToken).order_by(ApiToken.name)):
                used = f"{t.last_used_at:%Y-%m-%d %H:%M} UTC" if t.last_used_at else "jamais"
                print(f"{t.name}\tcréé {t.created_at:%Y-%m-%d}\tdernier usage {used}")
        elif args.action == "revoke":
            t = session.scalar(select(ApiToken).where(ApiToken.name == args.name))
            if t is None:
                print(f"Aucun jeton nommé « {args.name} »", file=sys.stderr)
                return 1
            session.delete(t)
            session.commit()
            print(f"Jeton « {args.name} » révoqué")
    return 0


def _import_ms(session, args) -> int:
    files = []
    for spec in args.qif:
        account, sep, path = spec.partition("=")
        if not sep:
            print(f"« {spec} » : format attendu COMPTE=FICHIER.qif", file=sys.stderr)
            return 2
        files.append(QifFile(account, decode(Path(path).read_bytes())))
    payees = decode(Path(args.payees).read_bytes()) if args.payees else None
    try:
        report = run_import(session, files, payees)
    except ImportFileError as err:
        session.rollback()
        print(f"Import refusé : {err}", file=sys.stderr)
        return 1
    if args.apply:
        session.commit()
    else:
        session.rollback()
    print(json.dumps({"dry_run": not args.apply, **report}, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
