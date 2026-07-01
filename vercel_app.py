"""Entrypoint de Vercel para el conector MCP.

Envuelve la app de `server_http.py` con un middleware ASGI que GARANTIZA que la
cabecera `Accept` incluya `application/json` y `text/event-stream`. El SDK de MCP
(Streamable HTTP) responde 406 "Client must accept text/event-stream" si el
cliente no las declara ambas; algunos clientes (incluido el flujo de alta de
conector) no las envian. Este wrapper lo normaliza sin tocar server_http.py.
"""
from server_http import app as _mcp_app


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


app = _EnsureAcceptMiddleware(_mcp_app)
