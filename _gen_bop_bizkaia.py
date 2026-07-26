# -*- coding: utf-8 -*-
"""Genera ordenanzas_data/bop_bizkaia_{municipios,config}.json a partir de:
  - _bizkaia_emisores.json  (emisores del BOB, sonda _probe_bizkaia2.py emisores)
  - _bizkaia_wiki_munis.json (lista canonica de municipios de Bizkaia: es/eu/oficial)
El "id" del mapa es el valor exacto que espera el campo _IYBIWBCC_issuersSelect
del buscador del BOB: nombres de emisor entrecomillados y unidos por ' o '.
"""
import json, re, sys, unicodedata, os

sys.stdout.reconfigure(encoding="utf-8")
AQUI = os.path.dirname(os.path.abspath(__file__))


def norm(s):
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s.lower())


emis = json.load(open(os.path.join(AQUI, "_bizkaia_emisores.json"), encoding="utf-8"))
wiki = json.load(open(os.path.join(AQUI, "_bizkaia_wiki_munis.json"), encoding="utf-8"))

# nombre preferente = denominacion oficial (col 3); variantes = es, eu, oficial
CANON = []
for es, eu, ofi in wiki:
    CANON.append({"oficial": ofi, "variantes": {es, eu, ofi}})

# alias extra: formas historicas / abreviadas con que el BOB nombra al ayuntamiento
ALIAS = {
    "arrankudiagazollo": ["Arrankudiaga"],
    "abantoycierbanaabantozierbena": [],
    "karrantzaharanavalledecarranza": ["Valle de Carranza", "Carranza"],
    "urdunaordunna": [],
    "truciosturtzioz": ["Trucíos", "Turtzioz"],
    "munitibararbatzegigerrikaitz": ["Munitibar", "Munitibar (Arbatzegi-Gerrikaitz)"],
    "valledetrapagatrapagaran": ["Valle de Trápaga"],
    "abantoyciervanaabantozierbena": ["Abanto y Ciérvana", "Abanto Zierbena"],
}
for c in CANON:
    for k, extra in ALIAS.items():
        if norm(c["oficial"]) == k:
            c["variantes"].update(extra)

# indice normalizado de variantes -> canonico
idx = {}
for c in CANON:
    for v in c["variantes"]:
        for cand in (v, v.split("/")[0], v.split("/")[-1], re.sub(r"\(.*?\)", "", v)):
            idx.setdefault(norm(cand), c)

AY = {k: v for k, v in emis.items() if k.lower().startswith("ayuntamiento de ")}

mapa = {}          # oficial -> {"nombres": [...], "codigos": [...]}
sin_match = []
for nombre, codigos in AY.items():
    bruto = nombre[len("Ayuntamiento de "):].strip()
    bruto_sin_prov = re.sub(r"\s*\([^)]*\)\s*$", "", bruto).strip()
    c = idx.get(norm(bruto)) or idx.get(norm(bruto_sin_prov))
    if not c and "(" not in bruto:           # sin parentesis: probar variantes sueltas
        c = idx.get(norm(re.sub(r"\(.*?\)", "", bruto)))
    if not c:
        sin_match.append((nombre, codigos))
        continue
    e = mapa.setdefault(c["oficial"], {"nombres": [], "codigos": []})
    e["nombres"].append(nombre)
    e["codigos"] += codigos

print("=== emisores 'Ayuntamiento de' NO reconocidos como municipio de Bizkaia (%d) ===" % len(sin_match))
for n, c in sorted(sin_match):
    print("  EXCLUIDO:", n, c)

falt = [c["oficial"] for c in CANON if c["oficial"] not in mapa]
print("\n=== municipios de Bizkaia SIN emisor en el BOB (%d) ===" % len(falt))
for f in falt:
    print("  FALTA:", f)

print("\nmunicipios mapeados:", len(mapa))
json.dump({k: v for k, v in sorted(mapa.items())},
          open(os.path.join(AQUI, "_bizkaia_mapa_bruto.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("-> _bizkaia_mapa_bruto.json")

# ---------------------------------------------------------------- JSON finales
# valor = cadena exacta que espera _IYBIWBCC_issuersSelect: "N1" o "N2"
final, vistos = {}, set()


def _añade(clave, valor):
    k = clave.strip()
    if not k or norm(k) in vistos:
        return
    vistos.add(norm(k))
    final[k] = valor


for c in CANON:
    ent = mapa.get(c["oficial"])
    if not ent:
        continue
    valor = " o ".join('"%s"' % n for n in sorted(set(ent["nombres"])))
    _añade(c["oficial"], valor)                      # denominación oficial
    for v in sorted(c["variantes"]):                 # es / eu / partes de "A/B"
        for parte in v.split("/"):
            _añade(re.sub(r"\(.*?\)", "", parte).strip(), valor)

DATA = os.path.join(AQUI, "ordenanzas_data")
json.dump(final, open(os.path.join(DATA, "bop_bizkaia_municipios.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1, sort_keys=True)
cfg = {"id": "bizkaia", "base": "https://www.bizkaia.eus",
       "mapa": "bop_bizkaia_municipios.json", "nombre": "Bizkaia",
       "familia": "bizkaia", "indice_desde": 1993}
CFG_PATH = os.path.join(DATA, "bop_bizkaia_config.json")
# conserva banderas de despliegue puestas a mano (p.ej. activo:false mientras no
# exista el backend "bizkaia" en bop_engine): re-generar NO debe reactivar la provincia
if os.path.exists(CFG_PATH):
    try:
        prev = json.load(open(CFG_PATH, encoding="utf-8"))
        for k in ("activo", "nota", "excluir"):
            if k in prev:
                cfg[k] = prev[k]
    except Exception:  # noqa: BLE001
        pass
json.dump(cfg, open(CFG_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("-> ordenanzas_data/bop_bizkaia_municipios.json  (%d claves, %d municipios)"
      % (len(final), len(mapa)))
print("-> ordenanzas_data/bop_bizkaia_config.json")
