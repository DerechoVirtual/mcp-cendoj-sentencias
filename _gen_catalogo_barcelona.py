# -*- coding: utf-8 -*-
"""
Genera ordenanzas_data/barcelona.json desde el dataset abierto del Ayuntamiento
de Barcelona "ordenances-municipals" (CKAN datastore), que cataloga las normas
del portal jurídico Norma (vLex) con su URL Akoma Ntoso por norma.

Texto oficial del portal: CATALÁN. Los títulos ES/CA y las keywords del dataset
se usan como alias para que las búsquedas en castellano acierten.

Script OFFLINE (excluido del deploy por `_*`):
    python _gen_catalogo_barcelona.py
"""
import json
import os
import re
import sys
import urllib.request

from _gen_comun import alias_para, norm

API = ("https://opendata-ajuntament.barcelona.cat/data/es/api/3/action/"
       "datastore_search?resource_id=e097b4cb-48b7-4557-9a45-3db221a60263&limit=500")
_HERE = os.path.dirname(os.path.abspath(__file__))
SALIDA = os.path.join(_HERE, "ordenanzas_data", "barcelona.json")

EXTRAS = [
    (r"civisme|convivencia", ["botellon", "consumo de alcohol en la via publica",
                              "convivencia", "civismo"]),
    (r"medi ambient|medio ambiente", ["ruido", "ruidos", "contaminacion acustica",
                                      "residuos", "limpieza", "calidad del aire",
                                      "emisiones", "aguas", "energia solar"]),
    (r"terrasses|terrazas", ["terraza", "terrazas", "veladores", "mesas y sillas",
                             "horario de terrazas"]),
    (r"circulacio|circulacion", ["movilidad", "trafico", "bicicleta", "patinete",
                                 "vmp", "estacionamiento", "aparcamiento", "zbe"]),
]


def getj(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (jurisprudenciator-gen)", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def main():
    recs = getj(API)["result"]["records"]
    print(f"registros del datastore: {len(recs)}")
    normas, sin_akn = [], 0
    for r in recs:
        akn = (r.get("Akoma_Ntoso_URL") or "").strip()
        m = re.search(r"/vid/(\d+)", akn)
        if not m:
            sin_akn += 1
            continue
        vid = m.group(1)
        tit_es = " ".join((r.get("Title_ES") or r.get("Title_CA") or "").split())
        tit_ca = " ".join((r.get("Title_CA") or "").split())
        kw = sorted({norm(k) for k in re.split(r"[;|]", (r.get("Keywords_ES") or "") + ";" +
                                               (r.get("Keywords_CA") or "")) if k.strip()})
        materia = " ".join((r.get("Subject_Matter_ES") or "").split())
        extras = []
        if tit_ca and tit_ca != tit_es:
            extras.append(norm(tit_ca))
        base = tit_es + " " + tit_ca
        for pat, al in EXTRAS:
            if re.search(pat, norm(base)):
                extras.extend(al)
        pub = f"publicada {r['Data_Publicacio']}" if r.get("Data_Publicacio") else ""
        if r.get("Data_Entrada_Vigor"):
            pub += (" · " if pub else "") + f"en vigor desde {r['Data_Entrada_Vigor']}"
        normas.append({
            "id": f"bcn-{vid}", "titulo": tit_es or tit_ca, "cat": materia or "General",
            "ref": "", "pub": pub, "mod": "",
            "alias": alias_para(base, extras), "kw": kw,
            "url": akn if akn.startswith("http") else f"https://ajuntament.barcelona.cat/norma-portal-juridic/vid/{vid}/akn",
            "web": f"https://ajuntament.barcelona.cat/norma-portal-juridic/es/#/vid/{vid}",
            "_fecha": r.get("Data_Publicacio") or "",
        })
    # dedupe por vid y por titulo-sin-año (las fiscales se repiten cada ejercicio:
    # nos quedamos con la de publicación más reciente)
    porid = {}
    for n in normas:
        porid.setdefault(n["id"], n)
    porclave = {}
    for n in porid.values():
        clave = re.sub(r"\b(19|20)\d{2}\b", "", norm(n["titulo"])).strip()
        prev = porclave.get(clave)
        if prev is None or n["_fecha"] > prev["_fecha"]:
            porclave[clave] = n
    normas = sorted(porclave.values(), key=lambda n: (n["cat"], n["titulo"]))
    for n in normas:
        n.pop("_fecha", None)
    fiscales = sum(1 for n in normas if re.search(r"fiscal|impost|impuesto|taxes|tasas",
                                                  n["titulo"], re.I))
    print(f"normas con AKN: {len(normas)} (fiscales/tributarias: {fiscales}) | sin AKN: {sin_akn}")

    catalogo = {
        "meta": {"municipio": "barcelona",
                 "fuente": "portal juridico del Ajuntament de Barcelona (Norma; texto oficial en catalan)",
                 "url": "https://ajuntament.barcelona.cat/norma-portal-juridic/es/"},
        "normas": normas,
    }
    os.makedirs(os.path.dirname(SALIDA), exist_ok=True)
    with open(SALIDA, "w", encoding="utf-8") as f:
        json.dump(catalogo, f, ensure_ascii=False, indent=1)
    print(f"OK -> {SALIDA} ({len(normas)} normas, {os.path.getsize(SALIDA)/1024:.0f} KB)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
