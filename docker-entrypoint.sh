#!/bin/sh
# Lancé en root (add-on Home Assistant, `docker run` sans --user) : prépare les dossiers puis abandonne root.
# Lancé avec un utilisateur (`user: "1000:1000"` dans compose) : exécute la commande telle quelle.
set -e
if [ "$(id -u)" = "0" ]; then
    data="${MYBANK_DATA_DIR:-/data}"
    if [ -z "${MYBANK_BACKUP_DIR:-}" ] && [ -n "${SUPERVISOR_TOKEN:-}" ]; then
        backups=/data/backups
    else
        backups="${MYBANK_BACKUP_DIR:-/backups}"
    fi
    mkdir -p "$data" "$backups"
    chown mybank:mybank "$data" "$backups"
    find "$data" "$backups" -maxdepth 1 -name 'my_bank*' ! -user mybank -exec chown mybank:mybank {} +
    [ -f "$data/home_assistant_token" ] && chown mybank:mybank "$data/home_assistant_token"
    # Option `api_token` de l'add-on : options.json appartient à root (0600), on la lit avant d'abandonner root.
    if [ -z "${MYBANK_API_TOKEN:-}" ] && [ -n "${SUPERVISOR_TOKEN:-}" ] && [ -f /data/options.json ]; then
        MYBANK_API_TOKEN="$(python -c 'import json; print(json.load(open("/data/options.json")).get("api_token") or "")')"
        export MYBANK_API_TOKEN
    fi
    # Option `docs` de l'add-on (documentation interactive /docs), lue pour la même raison.
    if [ -z "${MYBANK_DOCS:-}" ] && [ -n "${SUPERVISOR_TOKEN:-}" ] && [ -f /data/options.json ]; then
        MYBANK_DOCS="$(python -c 'import json; print(str(bool(json.load(open("/data/options.json")).get("docs"))).lower())')"
        export MYBANK_DOCS
    fi
    exec setpriv --reuid=mybank --regid=mybank --init-groups -- "$@"
fi
exec "$@"
