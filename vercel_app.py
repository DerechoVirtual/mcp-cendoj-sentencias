"""Entrypoint de Vercel para el conector MCP.

Envuelve la app de `server_http.py` con dos middlewares ASGI puros:

1. `_UserTokenMiddleware` — identificacion de usuario por URL PERSONAL:
   `/u/<token>/mcp` donde token = "v1.<base64url(email)>.<HMAC16>" firmado con
   CONNECTOR_TOKEN_SECRET (mismo secreto en el proyecto Vercel de la web, que es
   quien genera las URLs). Si el token es valido se reescribe el path a /mcp y se
   inyecta el email en el header interno `x-jpd-user` (la telemetria lo registra
   como user_email). Token invalido -> 401 con mensaje legible. La URL GENERICA
   /mcp NO se toca: sigue funcionando exactamente igual (anonima).
   Anti-spoofing: los headers `x-jpd-user`/`x-jpd-auth` ENTRANTES se eliminan
   SIEMPRE (solo este middleware puede ponerlos).

2. `_EnsureAcceptMiddleware` — GARANTIZA que la cabecera `Accept` incluya
   `application/json` y `text/event-stream`. El SDK de MCP (Streamable HTTP)
   responde 406 "Client must accept text/event-stream" si el cliente no las
   declara ambas; algunos clientes (incluido el flujo de alta de conector) no
   las envian. Este wrapper lo normaliza sin tocar server_http.py.
"""
import base64
import hashlib
import hmac
import json
import os
import re

from server_http import app as _mcp_app

_TOKEN_SECRET = (os.environ.get("CONNECTOR_TOKEN_SECRET") or "").strip().encode("utf-8")
_TOKEN_RE = re.compile(r"^/u/([^/]+)(/.*)?$")
_JPD_HEADERS = (b"x-jpd-user", b"x-jpd-auth")


def _validar_token(token: str) -> "str | None":
    """Valida "v1.<b64u(email)>.<HMAC16>" y devuelve el email, o None.
    Mismo algoritmo que connectorToken.ts en jurisprudenciator-web (vector de
    prueba cruzado: secreto 'CAMBIAR-ejemplo-32-chars-minimo!' + email
    'abogado@despacho.es' -> 'v1.YWJvZ2Fkb0BkZXNwYWNoby5lcw.82F7k0fWkJFX2rFO')."""
    if not _TOKEN_SECRET:
        return None
    try:
        version, payload, sig = token.split(".")
        if version != "v1" or not payload or not sig:
            return None
        firma = base64.urlsafe_b64encode(
            hmac.new(_TOKEN_SECRET, b"v1." + payload.encode("ascii"),
                     hashlib.sha256).digest()
        ).rstrip(b"=")[:16].decode("ascii")
        if not hmac.compare_digest(firma, sig):
            return None
        email = base64.urlsafe_b64decode(
            payload + "=" * (-len(payload) % 4)).decode("utf-8").strip()
        return email or None
    except Exception:  # noqa: BLE001
        return None


async def _responder_401(send, mensaje: str) -> None:
    body = json.dumps({"error": "invalid_token", "message": mensaje},
                      ensure_ascii=False).encode("utf-8")
    await send({"type": "http.response.start", "status": 401,
                "headers": [(b"content-type", b"application/json; charset=utf-8"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class _UserTokenMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            # Anti-spoofing: nadie de fuera puede fijar la identidad.
            headers = [(k, v) for k, v in scope.get("headers", [])
                       if k.lower() not in _JPD_HEADERS]
            path = scope.get("path", "") or ""
            m = _TOKEN_RE.match(path)
            if m:
                email = _validar_token(m.group(1))
                if not email:
                    await _responder_401(
                        send,
                        "URL personal no valida. Consigue la tuya gratis en "
                        "https://jurisprudenciator.lexiaipro.org/instalacion")
                    return
                resto = m.group(2) or "/mcp"
                scope = dict(scope)
                scope["path"] = resto
                if "raw_path" in scope:
                    scope["raw_path"] = resto.encode("utf-8")
                headers.append((b"x-jpd-user", email.encode("utf-8")))
                headers.append((b"x-jpd-auth", b"path"))
                scope["headers"] = headers
            else:
                scope = dict(scope)
                scope["headers"] = headers
        await self.app(scope, receive, send)


class _EnsureAcceptMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            REQUIRED = (b"application/json", b"text/event-stream")
            headers = list(scope.get("headers", []))
            out, seen = [], False
            for k, v in headers:
                if k.lower() == b"accept":
                    seen = True
                    val = v or b""
                    faltan = [m for m in REQUIRED if m not in val.lower()]
                    if faltan:
                        extra = b", ".join(faltan)
                        val = (val + b", " + extra) if val.strip() else extra
                    out.append((k, val))
                else:
                    out.append((k, v))
            if not seen:
                out.append((b"accept", b"application/json, text/event-stream"))
            scope = dict(scope)
            scope["headers"] = out
        await self.app(scope, receive, send)


app = _UserTokenMiddleware(_EnsureAcceptMiddleware(_mcp_app))
