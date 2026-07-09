# -*- coding: utf-8 -*-
"""
Genera ordenanzas_data/zaragoza.json desde la API JSON de la sede electrónica
del Ayuntamiento de Zaragoza (https://www.zaragoza.es/sede/servicio/normativa).

Script OFFLINE (excluido del deploy por `_*`). Reejecutar para refrescar:
    python _gen_catalogo_zaragoza.py
"""
import concurrent.futures as cf
import json
import os
import re
import sys
import urllib.request

from _gen_comun import alias_para, fecha_iso

LISTADO = "https://www.zaragoza.es/sede/servicio/normativa.json?rows=200"
DETALLE = "https://www.zaragoza.es/sede/servicio/normativa/{}.json"
_HERE = os.path.dirname(os.path.abspath(__file__))
SALIDA = os.path.join(_HERE, "ordenanzas_data", "zaragoza.json")

# Solo normativa PROPIA vigente con rango normativo municipal.
RANGOS_OK = {"ordenanza", "reglamento", "ordenanza fiscal"}
TITULO_OK = re.compile(r"^(ordenanza|reglamento)", re.I)
TITULO_MAL = re.compile(r"proyecto normativo|derogad", re.I)


def getj(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (jurisprudenciator-gen)", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def main():
    d = getj(LISTADO)
    candidatos, vistos = [], set()
    for cat in d["result"]:
        for n in cat.get("normativa", []):
            t = n.get("title", "")
            if n["id"] in vistos or not TITULO_OK.match(t) or TITULO_MAL.search(t):
                continue
            vistos.add(n["id"])
            candidatos.append((n["id"], t, cat["title"]))
    print(f"candidatos por titulo: {len(candidatos)} (de {sum(len(c.get('normativa', [])) for c in d['result'])})")

    def detalle(c):
        nid, titulo, cat = c
        try:
            det = getj(DETALLE.format(nid))
        except Exception as e:  # noqa: BLE001
            return ("ERR", nid, titulo, str(e))
        rango = ((det.get("rango") or {}).get("title") or "").lower()
        if det.get("municipal") == "N" or rango not in RANGOS_OK:
            return None
        # solo normas VIGENTES: las que estan en tramitacion (consulta previa /
        # informacion publica) solo tienen aprobacionInicial
        if not (det.get("publicacionFinal") or det.get("aprobacionFinal")):
            return None
        pubf = det.get("publicacionFinal") or det.get("publicacion") or {}
        pub = ""
        if isinstance(pubf, dict) and pubf.get("title"):
            pub = f"«{pubf['title']}» núm. {pubf.get('numero', '')}, de {fecha_iso(pubf.get('fecha', ''))}"
        texto_len = len(re.sub(r"<[^>]+>", "", det.get("text") or ""))
        anexos = [{"title": a.get("title", "PDF"), "link": a.get("link", "")}
                  for a in (det.get("anexos") or []) if a.get("link")]
        salida = {
            "id": f"zgz-{nid}", "titulo": titulo.strip(), "cat": cat,
            "ref": "", "pub": pub,
            "mod": fecha_iso(det.get("lastUpdated", "")),
            "alias": alias_para(titulo),
            "anexos": anexos, "texto_len": texto_len,
        }
        link = det.get("link") or ""
        if ".pdf" in link.lower():
            salida["url_pdf"] = link
        if texto_len < 200 and not anexos and not salida.get("url_pdf"):
            return ("SIN_TEXTO", nid, titulo, "ni text, ni anexos, ni link PDF")
        return salida

    normas, errores = [], []
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for r in ex.map(detalle, candidatos):
            if r is None:
                continue
            if isinstance(r, tuple):
                errores.append(r)
            else:
                normas.append(r)
    normas.sort(key=lambda n: (n["cat"], n["titulo"]))
    sin_texto = [n for n in normas if n["texto_len"] < 200]
    print(f"normas municipales vigentes: {len(normas)} | sin texto en ficha "
          f"(iran por PDF anexo): {len(sin_texto)} | errores: {len(errores)}")
    for e in errores[:5]:
        print("  ERR:", e[1], e[2][:60], e[3][:80])
    for n in sin_texto[:10]:
        print("  PDF:", n["id"], n["titulo"][:70], f"anexos={len(n['anexos'])}")

    catalogo = {
        "meta": {
            "municipio": "zaragoza",
            "fuente": "sede electronica del Ayuntamiento de Zaragoza (normativa vigente)",
            "url": "https://www.zaragoza.es/sede/servicio/normativa/",
        },
        "normas": normas,
    }
    os.makedirs(os.path.dirname(SALIDA), exist_ok=True)
    with open(SALIDA, "w", encoding="utf-8") as f:
        json.dump(catalogo, f, ensure_ascii=False, indent=1)
    print(f"OK -> {SALIDA} ({len(normas)} normas, {os.path.getsize(SALIDA)/1024:.0f} KB)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
