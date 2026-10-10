from fastapi import HTTPException

from .i18n import tr


def not_found(what: str, ident: int) -> HTTPException:
    """`what` : nom français de l'objet (« Compte », « Rubrique »…), traduit ici."""
    return HTTPException(404, tr("{what} {id} introuvable", what=tr(what), id=ident))


def conflict(message: str) -> HTTPException:
    return HTTPException(409, message)


def invalid(message: str) -> HTTPException:
    return HTTPException(422, message)
