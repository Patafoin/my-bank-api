"""Messages de l'API dans la langue du client (en-tête Accept-Language : fr, en, de ; défaut sans en-tête : fr).

Le texte français sert de clé, avec des champs nommés : tr("Le tiers « {name} » existe déjà", name=...).
Autre langue que fr/en/de : anglais. Dictionnaires : i18n_messages.py.
"""

from contextvars import ContextVar

from .i18n_messages import MESSAGES

LANGUAGES = ("fr", "en", "de")
_lang: ContextVar[str] = ContextVar("lang", default="fr")


def language_from_header(header: str | None) -> str:
    """Première langue de l'en-tête (« de-DE,de;q=0.9 » → de) ; anglais pour une langue non traduite."""
    if not header:
        return "fr"
    first = header.split(",")[0].split(";")[0].strip().lower()[:2]
    return first if first in LANGUAGES else "en"


def set_language(lang: str):
    return _lang.set(lang)


def reset_language(token) -> None:
    _lang.reset(token)


def tr(text: str, **fields) -> str:
    lang = _lang.get()
    out = text if lang == "fr" else MESSAGES.get(lang, {}).get(text) or MESSAGES["en"].get(text) or text
    return out.format(**fields) if fields else out
