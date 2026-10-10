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
    exec setpriv --reuid=mybank --regid=mybank --init-groups -- "$@"
fi
exec "$@"
