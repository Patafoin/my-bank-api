"""Jetons fournis au démarrage : variable `MYBANK_API_TOKEN`, ou add-on Home Assistant.

En add-on (variable `SUPERVISOR_TOKEN` présente), le service crée lui-même un jeton pour Home Assistant,
le garde dans `/data`, et l'annonce au Supervisor (« discovery ») : HA propose alors de configurer
l'intégration `my_bank` sans URL ni jeton à saisir.
"""

import json
import logging
import os
import time
import urllib.request

from sqlalchemy.orm import Session, sessionmaker

from .auth import ensure_token, new_token
from .config import Settings

log = logging.getLogger(__name__)

ENV_TOKEN_NAME = "MYBANK_API_TOKEN"
ADDON_TOKEN_NAME = "home-assistant-addon"
ADDON_TOKEN_FILE = "home_assistant_token"
DISCOVERY_SERVICE = "my_bank"
SUPERVISOR_URL = "http://supervisor"
INTERNAL_PORT = 8000


def apply_env_token(settings: Settings, make_session: sessionmaker[Session]) -> None:
    if settings.api_token:
        with make_session() as session:
            ensure_token(session, ENV_TOKEN_NAME, settings.api_token)


def addon_token(settings: Settings, make_session: sessionmaker[Session]) -> str:
    """Jeton réservé à HA, créé au premier démarrage de l'add-on puis réutilisé."""
    path = settings.data_dir / ADDON_TOKEN_FILE
    if path.exists():
        token = path.read_text().strip()
    else:
        token = new_token()
        path.write_text(token)
        path.chmod(0o600)
    with make_session() as session:
        ensure_token(session, ADDON_TOKEN_NAME, token)
    return token


def supervisor(method: str, path: str, body: dict | None = None) -> dict:
    request = urllib.request.Request(
        SUPERVISOR_URL + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {os.environ['SUPERVISOR_TOKEN']}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def announce(token: str, attempts: int = 5, delay: float = 5) -> bool:
    """Annonce l'add-on à Home Assistant. Quelques essais : le Supervisor peut être occupé au démarrage."""
    for attempt in range(1, attempts + 1):
        try:
            hostname = supervisor("GET", "/addons/self/info")["data"]["hostname"]
            config = {"host": hostname, "port": INTERNAL_PORT, "token": token}
            supervisor("POST", "/discovery", {"service": DISCOVERY_SERVICE, "config": config})
            log.info("Add-on annoncé à Home Assistant (%s:%s)", hostname, INTERNAL_PORT)
            return True
        except Exception as err:  # réseau, réponse inattendue : on réessaie puis on abandonne sans bloquer l'API
            log.warning("Annonce à Home Assistant impossible (essai %s/%s) : %s", attempt, attempts, err)
            if attempt < attempts:
                time.sleep(delay)
    return False


def run_addon_bootstrap(settings: Settings, make_session: sessionmaker[Session]) -> None:
    if os.environ.get("SUPERVISOR_TOKEN"):
        announce(addon_token(settings, make_session))
