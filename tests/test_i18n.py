import ast
import re
from pathlib import Path

from my_bank_api.i18n import language_from_header
from my_bank_api.i18n_messages import MESSAGES

SRC = Path(__file__).parent.parent / "src" / "my_bank_api"
FIELD = re.compile(r"\{(\w+)\}")
TRANSLATED_CONSTANTS = {"LOCKED", "PEER_LOCKED", "LABELS", "KIND_LABELS"}


def test_language_from_header():
    assert language_from_header(None) == "fr"  # sans en-tête : comportement historique
    assert language_from_header("de-DE,de;q=0.9,en;q=0.8") == "de"
    assert language_from_header("en-GB") == "en"
    assert language_from_header("it-IT") == "en"  # langue non traduite : anglais


def test_errors_follow_accept_language(client, make):
    make.account("Courant")
    body = {"name": "Courant"}
    assert client.post("/api/v1/accounts", json=body).json()["detail"] == "Le compte « Courant » existe déjà"
    r = client.post("/api/v1/accounts", json=body, headers={"Accept-Language": "de"})
    assert r.status_code == 409 and r.json()["detail"] == "Das Konto „Courant“ existiert bereits"
    r = client.get("/api/v1/accounts/999", headers={"Accept-Language": "en"})
    assert r.json()["detail"] == "Account 999 not found"


def test_validation_messages_are_translated(client, make):
    """Les validateurs Pydantic tournent dans la requête : leur message suit aussi la langue."""
    account = make.account()
    body = {"account_id": account["id"], "date": "2026-10-01", "amount": "abc"}
    r = client.post("/api/v1/transactions", json=body, headers={"Accept-Language": "en"})
    assert r.status_code == 422 and "invalid amount" in str(r.json()["detail"])


def _keys() -> set[str]:
    """Textes passés à tr() dans le code (littéraux), plus les noms d'objets passés à not_found()."""
    keys = set()
    for path in SRC.rglob("*.py"):
        if "migrations" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) in {"tr", "not_found"} and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    keys.add(arg.value)
            # textes français rangés dans des constantes, traduits au moment de lever l'erreur
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") in TRANSLATED_CONSTANTS for t in node.targets):
                value = node.value
                strings = value.values if isinstance(value, ast.Dict) else [value]
                keys.update(c.value for c in strings if isinstance(c, ast.Constant) and isinstance(c.value, str))
    return keys


def test_every_message_has_english_and_german():
    keys = _keys()
    assert len(keys) > 80
    for lang in ("en", "de"):
        missing = sorted(k for k in keys if k not in MESSAGES[lang])
        assert not missing, f"{lang}: {missing}"
        for key, text in MESSAGES[lang].items():
            assert set(FIELD.findall(text)) == set(FIELD.findall(key)), f"{lang}: {key}"
