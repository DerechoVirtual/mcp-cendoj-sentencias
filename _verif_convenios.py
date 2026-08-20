# -*- coding: utf-8 -*-
"""Verificacion del conector DESPLEGADO: convenios colectivos.

Lo que cuenta no es el banco local, sino el tiempo y el acierto A TRAVES DEL
SERVERLESS. Se llama por la URL personal (JSON-RPC del MCP), igual que lo hara
el Claude del abogado.

Uso: python _verif_convenios.py <host>          (host sin https://)
"""
import base64, hashlib, hmac, io, json, os, re, sys, time, unicodedata
import urllib.request
import concurrent.futures as cf

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

HOST = sys.argv[1] if len(sys.argv) > 1 else "mcp.jurisprudenciator.lexiaipro.org"
EMAIL = os.environ.get("JPD_EMAIL", "derechovirtualgpt@gmail.com")
LIMITE = 2.0


def _secreto():
    for linea in io.open(os.path.expanduser("~/.claude/.env"), encoding="utf-8",
                         errors="replace"):
        if linea.startswith("CONNECTOR_TOKEN_SECRET="):
            return linea.split("=", 1)[1].strip().strip('"')
    raise SystemExit("falta CONNECTOR_TOKEN_SECRET en el .env global")


def _token():
    # La firma va sobre b"v1." + payload, no sobre el payload a secas
    # (server_http._firmar_token / connectorToken.ts).
    payload = base64.urlsafe_b64encode(EMAIL.strip().lower().encode()).rstrip(b"=")
    firma = base64.urlsafe_b64encode(
        hmac.new(_secreto().encode(), b"v1." + payload, hashlib.sha256).digest()
    ).rstrip(b"=")[:16]
    return "v1.%s.%s" % (payload.decode(), firma.decode())


URL = "https://%s/u/%s/mcp" % (HOST, _token())


def llamar(tool, args, timeout=120):
    cuerpo = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                         "params": {"name": tool, "arguments": args}},
                        ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(URL, data=cuerpo, headers={
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json, text/event-stream"})
    t0 = time.time()
    try:
        crudo = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")
    except Exception as e:
        return time.time() - t0, "ERROR-HTTP %s: %s" % (type(e).__name__, str(e)[:120])
    dt = time.time() - t0
    for linea in crudo.splitlines():
        if linea.startswith("data:"):
            crudo = linea[5:].strip()
            break
    try:
        d = json.loads(crudo)
        partes = d.get("result", {}).get("content", [])
        return dt, "".join(p.get("text", "") for p in partes)
    except Exception:
        return dt, crudo[:400]


def norm(s):
    s = unicodedata.normalize('NFD', s or '')
    return ''.join(c for c in s if unicodedata.category(c) != 'Mn').lower()


# (pregunta, palabras que deben salir en el 1er resultado, territorio esperado)
CASOS = [
    ("convenio de hosteleria de la Comunidad de Madrid", ["hosteler"], "Madrid"),
    ("convenio del metal de Valencia", ["metal"], "Valencia"),
    ("convenio de la construccion de Barcelona", ["construc"], "Barcelona"),
    ("convenio de comercio de Sevilla", ["comerc"], "Sevilla"),
    ("convenio de limpieza de edificios y locales de Zaragoza", ["limpieza"], "Zaragoza"),
    ("convenio de oficinas y despachos de Bizkaia", ["oficina"], "Bizkaia"),
    ("convenio de hosteleria de Baleares", ["hosteler"], "Balears"),
    ("convenio del campo de Almeria", ["campo"], "Almeria"),
    ("convenio de transporte de mercancias de Murcia", ["transporte"], "Murcia"),
    ("convenio de siderometalurgia de A Coruna", ["sidero", "metal"], "Coruna"),
    ("convenio de hosteleria de Canarias", ["hosteler"], "Palmas"),
    ("convenio de comercio de Cantabria", ["comerc"], "Cantabria"),
    ("convenio de la construccion de Navarra", ["construc"], "Navarra"),
    ("convenio de peluquerias de Andalucia", ["peluquer"], "Andaluc"),
    ("convenio de oficinas y despachos de Castilla y Leon", ["oficina"], "Castilla"),
    ("convenio de hosteleria de Asturias", ["hosteler"], "Asturias"),
    ("convenio de limpieza de Castilla-La Mancha", ["limpieza"], "Castilla"),
    ("convenio de comercio de La Rioja", ["comerc"], "Rioja"),
    ("convenio de la construccion de Extremadura", ["construc"], "Extremadura"),
    ("convenio de hosteleria de Aragon", ["hosteler"], "Arag"),
    ("convenio estatal de dependencia", ["dependient"], "Estatal"),
    ("convenio estatal de empresas de seguridad", ["seguridad"], "Estatal"),
    ("convenio colectivo estatal de banca", ["banca"], "Estatal"),
    ("convenio de la industria quimica", ["quimic"], None),
    ("convenio de artes graficas estatal", ["graficas"], "Estatal"),
    ("convenio de hosteleria de Tenerife", ["hosteler"], "Tenerife"),
    ("convenio de comercio de Gipuzkoa", ["comerc"], "Gipuzkoa"),
    ("convenio de transporte de viajeros de Malaga", ["viajeros", "transporte"], "Malaga"),
    ("convenio de panaderias de Ceuta", ["panader"], None),
    ("convenio de la vid de Cadiz", ["vid", "vinicola"], "Cadiz"),
]


def caso(c):
    pregunta, esperadas, terr = c
    dt, r = llamar("buscar_convenio", {"consulta": pregunta})
    m = re.search(r"^1\. (.+)$", r, re.M)
    prim = m.group(1) if m else ""
    acierta = bool(prim) and any(e in norm(prim) for e in esperadas)
    terr_ok = terr is None or norm(terr) in norm(r[:400])
    tiene_url = "Texto oficial: http" in r
    tiene_cod = bool(re.search(r"Codigo de convenio: \d{10,}", r))
    return (pregunta, dt, acierta and terr_ok and tiene_url and tiene_cod, prim,
            "" if acierta else ("sin 1er resultado" if not prim else "1o inesperado"))


print("VERIFICACION EN %s" % HOST)
print("=" * 118)
t_ini = time.time()
with cf.ThreadPoolExecutor(max_workers=4) as ex:
    res = list(ex.map(caso, CASOS))

ok = lentos = 0
tiempos = []
for pregunta, dt, bien, prim, nota in res:
    tiempos.append(dt)
    marca = "OK " if bien else "MAL"
    if dt > LIMITE:
        marca = "LENTO"
        lentos += 1
    elif bien:
        ok += 1
    print("%-52s %6.2fs  %-5s %s" % (pregunta[:51], dt, marca, (prim or nota)[:48]))

tiempos.sort()
print("=" * 118)
print("ACIERTOS %d/%d   fuera de los %.0f s: %d" % (ok, len(CASOS), LIMITE, lentos))
print("latencia serverless: min=%.2fs  mediana=%.2fs  p95=%.2fs  max=%.2fs  (total %.0fs)"
      % (tiempos[0], tiempos[len(tiempos) // 2], tiempos[int(len(tiempos) * 0.95)],
         tiempos[-1], time.time() - t_ini))

print()
print("--- leer_convenio (texto oficial) ---")
dt, r = llamar("leer_convenio", {"codigo": "28002085011981", "articulo": "20"})
print("art. 20 hosteleria Madrid: %.2fs | %d chars | %s"
      % (dt, len(r), "OK" if "ARTICULO 20" in r else "MAL"))
dt, r = llamar("leer_convenio", {"consulta": "hosteleria", "territorio": "Sevilla",
                                 "buscar_en": "vacaciones"})
print("vacaciones hosteleria Sevilla: %.2fs | %d chars | %s"
      % (dt, len(r), "OK" if "PASAJES" in r or "vacaciones" in norm(r) else "MAL"))

print()
print("--- vigencia_convenio ---")
dt, r = llamar("vigencia_convenio", {"codigo": "28002085011981"})
print("%.2fs | %s" % (dt, "OK" if "TRAMITES" in r else "MAL: " + r[:150]))

print()
print("--- buscar_convenio en_texto (texto integro, en vivo) ---")
dt, r = llamar("buscar_convenio", {"consulta": "nocturnidad", "territorio": "Madrid",
                                   "en_texto": "si", "maximo": 5})
print("%.2fs | %s" % (dt, "OK" if "TEXTO CONTIENE" in r else "MAL: " + r[:150]))

sys.exit(0 if (ok == len(CASOS)) else 1)
