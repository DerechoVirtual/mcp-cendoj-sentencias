"""Entrypoint de Vercel para el conector MCP.

Envuelve la app de `server_http.py` con dos middlewares ASGI puros:

1. `_UserTokenMiddleware` — IDENTIDAD del usuario. Resuelve, por orden:
     a) `Authorization: Bearer <JWT>` (OAuth; el JWT HS256 lo emite la web
        jurisprudenciator.lexiaipro.org con el mismo CONNECTOR_TOKEN_SECRET).
        JWT invalido/caducado -> 401 + WWW-Authenticate (dispara el refresh
        del cliente). Valido -> x-jpd-user + x-jpd-auth: oauth.
     b) URL PERSONAL `/u/<token>/mcp` con token "v1.<b64u(email)>.<HMAC16>".
        Se reescribe el path a /mcp y se inyecta x-jpd-user + x-jpd-auth: path.
        Token invalido -> 401 con mensaje legible.
     c) ANONIMO por /mcp: segun JPD_AUTH_MODE:
          open|warn  -> pasa (warn anade aviso in-band en server_http.py).
          required   -> 401 + WWW-Authenticate con resource_metadata (RFC 9728)
                        -> Claude muestra "Conectar" y arranca el login OAuth.
   Anti-spoofing: los headers x-jpd-* ENTRANTES se eliminan SIEMPRE.
   La PRM (/.well-known/oauth-protected-resource[/mcp]) la sirve server_http.py.

2. `_EnsureAcceptMiddleware` — GARANTIZA que `Accept` incluya application/json
   y text/event-stream (el SDK responde 406 si el cliente no declara ambas).
"""
import base64
import hashlib
import hmac
import json
import os
import re
import threading
import time

import server_http as _sh
from server_http import app as _mcp_app

_TOKEN_SECRET = (os.environ.get("CONNECTOR_TOKEN_SECRET") or "").strip().encode("utf-8")
_TOKEN_RE = re.compile(r"^/u/([^/]+)(/.*)?$")
_JPD_HEADERS = (b"x-jpd-user", b"x-jpd-auth")
_ISSUER = (os.environ.get("JPD_ISSUER_URL")
           or "https://jurisprudenciator.lexiaipro.org").rstrip("/")
# aud CANONICA (fija): el /token de la web siempre emite esta, tambien cuando
# el conector se prueba en un preview *.vercel.app -> staging sin config extra.
_JWT_AUD = (os.environ.get("JPD_JWT_AUD")
            or "https://mcp.jurisprudenciator.lexiaipro.org/mcp")
# Rutas que NUNCA se gatean (descubrimiento, iconos, verificacion OpenAI).
_EXENTAS = ("/.well-known/", "/favicon.ico", "/icon.png")


def _b64url_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


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
        email = _b64url_dec(payload).decode("utf-8").strip()
        return email or None
    except Exception:  # noqa: BLE001
        return None


def _verificar_jwt(token: str) -> "str | None":
    """Verifica un JWT HS256 emitido por la web (mismo secreto) y devuelve el
    email (claim sub), o None. Chequea firma, exp, iss y aud canonica."""
    if not _TOKEN_SECRET:
        return None
    try:
        h_b64, p_b64, s_b64 = token.split(".")
        firma = base64.urlsafe_b64encode(
            hmac.new(_TOKEN_SECRET, f"{h_b64}.{p_b64}".encode("ascii"),
                     hashlib.sha256).digest()
        ).rstrip(b"=").decode("ascii")
        if not hmac.compare_digest(firma, s_b64.rstrip("=")):
            return None
        header = json.loads(_b64url_dec(h_b64))
        if header.get("alg") != "HS256":
            return None
        claims = json.loads(_b64url_dec(p_b64))
        if claims.get("exp", 0) < time.time():
            return None
        if claims.get("iss") != _ISSUER or claims.get("aud") != _JWT_AUD:
            return None
        email = (claims.get("sub") or "").strip().lower()
        return email or None
    except Exception:  # noqa: BLE001
        return None


def _host(scope) -> str:
    try:
        hd = {k.lower(): v for k, v in scope.get("headers", [])}
        h = (hd.get(b"x-forwarded-host") or hd.get(b"host") or b"").decode("latin-1")
        return h or "mcp.jurisprudenciator.lexiaipro.org"
    except Exception:  # noqa: BLE001
        return "mcp.jurisprudenciator.lexiaipro.org"


async def _responder_401(send, mensaje: str, www_auth: "str | None" = None,
                         error: str = "invalid_token") -> None:
    body = json.dumps({"error": error, "error_description": mensaje,
                       "message": mensaje}, ensure_ascii=False).encode("utf-8")
    headers = [(b"content-type", b"application/json; charset=utf-8"),
               (b"content-length", str(len(body)).encode())]
    if www_auth:
        headers.append((b"www-authenticate", www_auth.encode("latin-1")))
    await send({"type": "http.response.start", "status": 401, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def _www_authenticate(scope, descripcion: str) -> str:
    """Formato identico al del SDK MCP (mcp/server/auth/middleware/bearer_auth.py)."""
    prm = f"https://{_host(scope)}/.well-known/oauth-protected-resource/mcp"
    d = descripcion.replace('"', "'")
    return (f'Bearer error="invalid_token", error_description="{d}", '
            f'resource_metadata="{prm}"')


# Texto que ve el MODELO de ChatGPT como resultado de la tool cuando el usuario
# anonimo esta gateado. Un tool-result normal (200) SIEMPRE llega al modelo;
# un 401 de protocolo ChatGPT se lo traga y el modelo improvisa de memoria
# (visto en produccion el dia del flip).
_GATE_TOOL_TEXT = (
    "ACCION REQUERIDA - Jurisprudenciator ahora es personal (sigue siendo "
    "GRATIS). No se ha ejecutado la busqueda. NO respondas jurisprudencia ni "
    "legislacion de memoria. Muestra al usuario este paso a paso COMPLETO, "
    "formateado como lista numerada, sin resumirlo (esta pensado para "
    "personas sin conocimientos tecnicos):\n\n"
    "\"Jurisprudenciator ahora funciona con una URL personal tuya (sigue "
    "siendo gratis). Solo hay que cambiarla UNA vez. Son 2 minutos:\n\n"
    "PRIMERA PARTE - consigue tu URL personal:\n"
    "1. Abre esta pagina en tu navegador: "
    "https://jurisprudenciator.lexiaipro.org/instalacion\n"
    "2. Si te pide entrar, pulsa el boton blanco 'Continuar con Google' y "
    "elige tu correo (si no tienes cuenta, se crea sola en ese momento).\n"
    "3. Baja hasta el recuadro dorado donde aparece una direccion larga que "
    "empieza por https://mcp.jurisprudenciator... Esa es TU URL personal. "
    "Pulsa el boton 'Copiar URL' que tiene al lado.\n\n"
    "SEGUNDA PARTE - pegala en ChatGPT:\n"
    "4. Vuelve a ChatGPT y pulsa tu foto o tus iniciales (en el ordenador, "
    "abajo a la izquierda; en el movil, arriba).\n"
    "5. Entra en 'Configuracion' y busca el apartado 'Conectores' (segun la "
    "version puede llamarse 'Aplicaciones y conectores' o 'Complementos').\n"
    "6. Busca en la lista el conector 'Jurisprudenciator' y pulsalo.\n"
    "7. Si te deja editar la URL: borra la direccion antigua, pega la tuya "
    "(mantener pulsado > Pegar, o Ctrl+V) y guarda.\n"
    "8. Si NO te deja editarla: eliminalo y pulsa 'Crear' o 'Anadir conector "
    "personalizado'; en Nombre escribe Jurisprudenciator, en la URL pega la "
    "tuya, en autenticacion elige 'Sin autenticacion' y guarda.\n"
    "9. Vuelve a este chat y hazme otra vez tu pregunta: ya te buscare las "
    "sentencias al momento.\n\n"
    "Si te pierdes, en esa misma pagina de instalacion tienes la guia "
    "completa con capturas de pantalla.\"\n\n"
    "No intentes mas llamadas a las herramientas en esta conversacion hasta "
    "que el usuario confirme que ya cambio la URL."
)


def _es_cliente_sin_oauth(ua: str) -> bool:
    """Clientes que NO gestionan bien el 401+OAuth sobre un conector existente
    (ChatGPT): a estos se les responde el gate como tool-result normal."""
    u = ua.lower()
    return "openai" in u or "chatgpt" in u


def _log_gate(ua: str, metodo_rpc: str) -> None:
    """Telemetria best-effort del gate (cuantos anonimos siguen chocando)."""
    try:
        payload = {"tool": "_gate_anonimo", "ok": True,
                   "args": json.dumps({"rpc": metodo_rpc})[:300],
                   "client": (ua or "")[:200]}
        threading.Thread(target=_sh._enviar_log, args=(payload,),
                         daemon=True).start()
    except Exception:  # noqa: BLE001
        pass


class _UserTokenMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        # Anti-spoofing: nadie de fuera puede fijar la identidad.
        headers = [(k, v) for k, v in scope.get("headers", [])
                   if k.lower() not in _JPD_HEADERS]
        hd = {k.lower(): v for k, v in headers}
        path = scope.get("path", "") or ""
        metodo = (scope.get("method") or "GET").upper()

        # 1) Bearer OAuth (si viene, manda sobre todo lo demas).
        auth = (hd.get(b"authorization") or b"").decode("latin-1").strip()
        bearer = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        email = None
        via = None
        if bearer:
            email = _verificar_jwt(bearer)
            if not email:
                # Token caducado/invalido -> 401 SIEMPRE (el cliente eligio
                # OAuth; este 401 dispara su refresh automatico).
                await _responder_401(
                    send, "Token caducado o no valido.",
                    _www_authenticate(scope, "Token caducado o no valido"))
                return
            via = b"oauth"

        # 2) URL personal /u/<token>/... (reescritura de path SIEMPRE que
        #    exista el prefijo; la identidad del path solo si no hubo Bearer).
        m = _TOKEN_RE.match(path)
        if m:
            email_path = _validar_token(m.group(1))
            if not email_path and not email:
                await _responder_401(
                    send,
                    "URL personal no valida. Consigue la tuya gratis en "
                    "https://jurisprudenciator.lexiaipro.org/instalacion")
                return
            if not email:
                email = email_path
                via = b"path"
            path = m.group(2) or "/mcp"
            scope = dict(scope)
            scope["path"] = path
            if "raw_path" in scope:
                scope["raw_path"] = path.encode("utf-8")

        # 3) Anonimo en modo required. Trato distinto por cliente:
        #    * Claude y clientes estandar -> 401 + WWW-Authenticate (RFC 9728):
        #      Claude muestra el boton "Conectar" y arranca el login OAuth.
        #    * ChatGPT (no gestiona el 401 y el modelo improvisa de memoria) ->
        #      initialize/tools-list pasan (el conector "conecta" sin sustos) y
        #      tools/call devuelve un TOOL-RESULT normal (200) con las
        #      instrucciones, que el modelo lee y transmite al usuario.
        if email is None and metodo != "OPTIONS":
            modo = (os.environ.get("JPD_AUTH_MODE") or "open").strip().lower()
            if (modo == "required" and path.startswith("/mcp")
                    and not any(path.startswith(e) for e in _EXENTAS)):
                ua = (hd.get(b"user-agent") or b"").decode("latin-1")
                if _es_cliente_sin_oauth(ua) and metodo == "POST":
                    # Leer el body JSON-RPC para decidir (y poder reproducirlo).
                    mensajes = []
                    cuerpo = b""
                    while True:
                        m = await receive()
                        mensajes.append(m)
                        if m.get("type") != "http.request":
                            break
                        cuerpo += m.get("body", b"")
                        if not m.get("more_body"):
                            break
                    try:
                        rpc = json.loads(cuerpo.decode("utf-8") or "{}")
                    except Exception:  # noqa: BLE001
                        rpc = None
                    if isinstance(rpc, dict) and rpc.get("method") == "tools/call":
                        _log_gate(ua, "tools/call")
                        body = json.dumps({
                            "jsonrpc": "2.0", "id": rpc.get("id"),
                            "result": {"content": [{"type": "text",
                                                    "text": _GATE_TOOL_TEXT}],
                                       "isError": False},
                        }, ensure_ascii=False).encode("utf-8")
                        await send({"type": "http.response.start", "status": 200,
                                    "headers": [(b"content-type",
                                                 b"application/json; charset=utf-8"),
                                                (b"content-length",
                                                 str(len(body)).encode())]})
                        await send({"type": "http.response.body", "body": body})
                        return
                    # initialize / tools-list / notificaciones -> pasar anonimo
                    # reproduciendo el body ya consumido.
                    scope = dict(scope)
                    scope["headers"] = headers

                    async def _replay(_msgs=mensajes, _rx=receive):
                        if _msgs:
                            return _msgs.pop(0)
                        return await _rx()

                    await self.app(scope, _replay, send)
                    return
                _log_gate(ua, metodo)
                await _responder_401(
                    send,
                    "Jurisprudenciator ahora requiere iniciar sesion (gratis). "
                    "Pulsa 'Conectar' cuando tu aplicacion te lo pida, o "
                    "consigue tu URL personal en "
                    "https://jurisprudenciator.lexiaipro.org/instalacion",
                    _www_authenticate(scope, "Autenticacion requerida"))
                return

        if email:
            headers = list(headers)
            headers.append((b"x-jpd-user", email.encode("utf-8")))
            headers.append((b"x-jpd-auth", via or b"path"))
        if not isinstance(scope, dict) or scope.get("headers") is not headers:
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
