# -*- coding: utf-8 -*-
"""
Genera ordenanzas_data/valencia.json desde la sede electrónica del Ayuntamiento
de València (https://sede.valencia.es/sede/ordenanzas/index.xhtml).

Peculiaridades verificadas (09-jul-2026):
  * El LISTADO (154 normas) es server-rendered y carga en <1 s, PERO las fichas
    de detalle SOLO responden con la cookie de sesión JSF del listado y el
    header Referer; sin ellos el servidor se cuelga (timeout, ni 4xx).
  * Cada ficha publica el "Texto vigente de esta Norma Municipal" como PDF en
    https://sede.valencia.es/sede/descarga/doc/DOCUMENT_1_<id> — y ESA URL de
    descarga sí es pública y estable (sin sesión, 0,6 s, PDF limpio).

Script OFFLINE (excluido del deploy por `_*`):
    python _gen_catalogo_valencia.py
"""
import concurrent.futures as cf
import base64
import json
import os
import re
import sys
import html as H
import http.cookiejar
import urllib.request

from _gen_comun import alias_para, norm

# Alias EXTRA por norma (regex sobre el título normalizado): materias que en
# València viven en normas cuyo título no las delata.
EXTRAS = [
    (r"ocupacion de dominio publico municipal",
     ["terraza", "terrazas", "veladores", "mesas y sillas", "terraza de bar",
      "horario de terrazas", "quioscos", "churrerias"]),
    (r"convivencia y civismo", ["botellon", "grafitis", "mendicidad"]),
]

BASE = "https://sede.valencia.es"
LISTADO = BASE + "/sede/ordenanzas/index.xhtml?lang=1"
_HERE = os.path.dirname(os.path.abspath(__file__))
SALIDA = os.path.join(_HERE, "ordenanzas_data", "valencia.json")

import threading
_lock = threading.Lock()
_op = None


def _nueva_sesion():
    global _op
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", "Mozilla/5.0 (jurisprudenciator-gen)"),
                     ("Referer", LISTADO), ("Accept-Language", "es-ES,es")]
    # sembrar la cookie JSF con el listado (sin ella, las fichas se cuelgan)
    with op.open(LISTADO, timeout=40) as r:
        cuerpo = r.read().decode("utf-8", "replace")
    _op = op
    return cuerpo


def get(url, timeout=40):
    """GET con hasta 3 intentos; si la sesión JSF caduca/cuelga, se renueva."""
    for intento in range(3):
        try:
            with _op.open(url, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            if intento == 2:
                raise
            with _lock:
                try:
                    _nueva_sesion()
                except Exception:  # noqa: BLE001
                    pass


def _id_de(path: str) -> str:
    b64 = path.rsplit("/", 1)[-1].split(".")[0]
    try:
        return "val-" + base64.b64decode(b64 + "=" * (-len(b64) % 4)).decode("ascii")
    except Exception:  # noqa: BLE001
        return "val-" + re.sub(r"[^A-Za-z0-9]", "", b64)[:12]


def main():
    lst = _nueva_sesion()
    vistos, normas_in = set(), []
    for m in re.finditer(r'href="(/sede/ordenanzas/detalle/[^"]+)"[^>]*>([^<]{5,160})', lst):
        path, titulo = m.group(1), " ".join(H.unescape(m.group(2)).split())
        nid = _id_de(path)
        if nid in vistos:
            continue
        vistos.add(nid)
        normas_in.append((nid, titulo, path))
    print(f"normas en el listado: {len(normas_in)}")

    def detalle(entrada):
        nid, titulo, path = entrada
        try:
            h = get(BASE + path, timeout=40)
        except Exception as e:  # noqa: BLE001
            return ("ERR", nid, titulo, str(e)[:90])
        # PDFs candidatos: el primero tras "Texto vigente" y los siguientes
        # (algunas fichas ponen de primero una caratula sin articulado)
        docs = [(m.start(), m.group(1)) for m in re.finditer(
            r'href="(https://sede\.valencia\.es/sede/descarga/doc/[^"]+)"', h)]
        if not docs:
            return ("SIN_DOC", nid, titulo, "")
        marca = h.find("Texto vigente")
        candidatos = [u for pos, u in docs if pos > marca] if marca > 0 else [u for _, u in docs]
        if not candidatos:
            candidatos = [u for _, u in docs]
        url_doc, urls = candidatos[0], candidatos[:4]
        # publicación BOP si aparece (sobre el TEXTO, nunca sobre el HTML crudo)
        pub = ""
        texto = re.sub(r"\s+", " ", H.unescape(re.sub(r"<[^>]+>", " ", h)))
        mp = re.search(r"(BOP\s*(?:n[uú]mero|n[.º°]*)?\s*\d[^,;]{0,40}?de fecha \d{1,2}/\d{1,2}/\d{2,4})", texto)
        if mp:
            pub = " ".join(mp.group(1).split())[:90]
        cat = "Fiscal / tributos" if re.search(r"impuesto|tasas?|contribucion|fiscal",
                                               titulo, re.I) else "General"
        extras = []
        for pat, al in EXTRAS:
            if re.search(pat, norm(titulo)):
                extras.extend(al)
        return {"id": nid, "titulo": titulo, "cat": cat, "ref": "", "pub": pub,
                "mod": "", "alias": alias_para(titulo, extras), "url": url_doc,
                "urls": urls, "formato": "pdf"}

    normas, errores = [], []
    with cf.ThreadPoolExecutor(max_workers=2) as ex:  # suave: la sede ratelimita
        for r in ex.map(detalle, normas_in):
            (errores if isinstance(r, tuple) else normas).append(r)
    normas.sort(key=lambda n: (n["cat"], n["titulo"]))
    print(f"normas con PDF consolidado: {len(normas)} | incidencias: {len(errores)}")
    for e in errores[:8]:
        print("  ", e[0], e[1], e[2][:60], e[3])

    catalogo = {
        "meta": {"municipio": "valencia",
                 "fuente": "sede electronica del Ayuntamiento de Valencia (texto consolidado vigente)",
                 "url": "https://sede.valencia.es/sede/ordenanzas/"},
        "normas": normas,
    }
    os.makedirs(os.path.dirname(SALIDA), exist_ok=True)
    with open(SALIDA, "w", encoding="utf-8") as f:
        json.dump(catalogo, f, ensure_ascii=False, indent=1)
    print(f"OK -> {SALIDA} ({len(normas)} normas, {os.path.getsize(SALIDA)/1024:.0f} KB)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
