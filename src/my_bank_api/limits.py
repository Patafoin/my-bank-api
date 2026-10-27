"""Taille maximale d'une requête, contrôlée avant que le corps ne soit lu en entier.

Sans elle, un envoi démesuré (import, justificatif) serait chargé en mémoire avant tout contrôle et pourrait
dépasser la limite mémoire du conteneur. Le plafond par justificatif (MAX_ATTACHMENT_SIZE) reste vérifié ensuite.
"""

import json

from fastapi import HTTPException

from .i18n import language_from_header, reset_language, set_language, tr
from .schemas import MAX_ATTACHMENT_SIZE

# Un justificatif au plafond, en base64 (+ un tiers), plus la marge du JSON qui l'entoure.
MAX_REQUEST_SIZE = MAX_ATTACHMENT_SIZE * 4 // 3 + 1024 * 1024


def _message(limit: int) -> str:
    return tr("Requête trop volumineuse (plus de {size} Mio)", size=limit // (1024 * 1024))


class BodySizeLimit:
    """Middleware ASGI : 413 dès que le corps annoncé (Content-Length) ou reçu dépasse `limit` octets."""

    def __init__(self, app, limit: int = MAX_REQUEST_SIZE) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        length = headers.get(b"content-length", b"")
        if length.isdigit() and int(length) > self.limit:
            return await self._reject(headers, send)

        received = 0

        async def counted_receive():
            # Corps sans Content-Length (envoi par morceaux) : arrêt dès le dépassement. FastAPI laisse passer une
            # HTTPException levée pendant la lecture du corps ; elle devient une réponse 413 normale.
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.limit:
                    raise HTTPException(413, _message(self.limit))
            return message

        await self.app(scope, counted_receive, send)

    async def _reject(self, headers: dict, send) -> None:
        # Avant toute route : la langue de la requête n'est pas encore fixée par le middleware de main.py.
        token = set_language(language_from_header(headers.get(b"accept-language", b"").decode("latin-1") or None))
        try:
            detail = _message(self.limit)
        finally:
            reset_language(token)
        body = json.dumps({"detail": detail}, ensure_ascii=False).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})
