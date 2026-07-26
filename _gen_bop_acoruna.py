# -*- coding: utf-8 -*-
"""Genera ordenanzas_data/bop_acoruna_{municipios,config}.json.

Solo los 93 CONCELLOS de la provincia de A Coruna. Se valida contra la lista
canonica (INE/IGE) y se excluye explicitamente cualquier anunciante forastero
(Vigo, Pontevedra, Ourense, Lugo...) que publique en este BOP.
"""
import json, os, re, sys, unicodedata

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# --- 93 concellos oficiales de la provincia de A Coruna (nombre oficial en gallego)
CANON = """A Baña|A Capela|A Coruña|A Laracha|A Pobra do Caramiñal|Abegondo|Ames|Aranga|Ares|Arteixo|
Arzúa|As Pontes de García Rodríguez|As Somozas|Bergondo|Betanzos|Boimorto|Boiro|Boqueixón|Brión|
Cabana de Bergantiños|Cabanas|Camariñas|Cambre|Carballo|Cariño|Carnota|Carral|Cedeira|Cee|Cerceda|
Cerdido|Coirós|Corcubión|Coristanco|Culleredo|Curtis|Dodro|Dumbría|Fene|Ferrol|Fisterra|Frades|
Irixoa|Laxe|Lousame|Malpica de Bergantiños|Mañón|Mazaricos|Melide|Mesía|Miño|Moeche|Monfero|
Mugardos|Muros|Muxía|Narón|Neda|Negreira|Noia|O Pino|Oleiros|Ordes|Oroso|Ortigueira|Outes|
Oza-Cesuras|Paderne|Padrón|Ponteceso|Pontedeume|Porto do Son|Rianxo|Ribeira|Rois|Sada|
San Sadurniño|Santa Comba|Santiago de Compostela|Santiso|Sobrado|Teo|Toques|Tordoia|Touro|Trazo|
Val do Dubra|Valdoviño|Vedra|Vilarmaior|Vilasantar|Vimianzo|Zas"""
CANON = [x.strip() for x in CANON.replace("\n", "").split("|") if x.strip()]

# A Coruna capital: el anunciante raiz (8521) solo cubre ~2020->hoy; la normativa
# anterior cuelga de sus areas/servizos. Se anaden los sub-anunciantes que SI
# publican ordenanzas/regulamentos (>=3 normas en el titulo). Verificado en vivo.
CAPITAL_IDS = ["8521", "212", "1511", "211", "1596", "1515", "3220", "1566", "202", "1578", "7341"]
# Oza-Cesuras (fusion 2013): se anaden los dos concellos historicos, ambos de A Coruna.
OZA_IDS = ["5581", "103", "125"]


def norm(s):
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.replace("-", " ").replace("'", " ")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9ñ ]", " ", s)).strip()


ART = ("a ", "o ", "as ", "os ", "la ", "el ", "las ", "los ")


def clave(s):
    """normaliza quitando articulo inicial para casar 'A Bana' <-> 'Bana'."""
    n = norm(s)
    for a in ART:
        if n.startswith(a):
            return n[len(a):]
    return n


raw = json.load(open("_ac_concello_all.json", encoding="utf-8"))
# solo entradas "CONCELLO D* <nombre>" sin sub-organo
port = {}
for nm, i in raw.items():
    m = re.match(r"^CONCELLO D(?:E|A|O|AS|OS)\s+(.+)$", nm)
    if m and "." not in nm:
        port.setdefault(clave(m.group(1)), []).append((nm, i))

mapa, faltan = {}, []
for c in CANON:
    if c == "A Coruña":
        mapa[c] = ",".join(CAPITAL_IDS); continue
    if c == "Oza-Cesuras":
        mapa[c] = ",".join(OZA_IDS); continue
    hit = port.get(clave(c))
    if not hit:
        faltan.append(c); continue
    mapa[c] = str(hit[0][1])

print("canonicos: %d   mapeados: %d   faltan: %s" % (len(CANON), len(mapa), faltan))
assert not faltan, faltan
assert len(CANON) == 93, len(CANON)
assert len(mapa) == 93, len(mapa)

# --- guardas anti-secuestro: ningun id de concello FORASTERO
FORASTEROS = {  # verificados en el listado del portal, NO son de A Coruna
    "5881": "A Illa de Arousa (PO)", "5741": "Baleira (LU)", "4880": "Bóveda (LU)",
    "2740": "Bueu (PO)", "1690": "Caldas de Reis (PO)", "1763": "Cuntis (PO)",
    "1588": "Ourense (OU)", "3821": "Pontecesures (PO)", "3020": "Pontevedra (PO)",
    "5200": "Sanxenxo (PO)", "6021": "Tomiño (PO)", "6661": "Valga (PO)",
    "4820": "Vigo (PO)", "1530": "Vilagarcía de Arousa (PO)", "5460": "Vilalba (LU)",
    "1840": "Cudillero (AS)", "3540": "Deltebre (T)", "5380": "La Garrovilla (BA)",
    "7521": "La Puebla de Cazalla (SE)", "1706": "Terrassa (B)",
    "423": "Instituto Mpal Facenda Ferrol", "1563": "Patronato Deportes Ferrol",
    "275": "Mancomunidade Ferrol", "273": "Mancomunidade Tambre", "274": "Mancomunidade Barbanza",
}
usados = {i for v in mapa.values() for i in v.split(",")}
mal = usados & set(FORASTEROS)
assert not mal, "IDs forasteros colados: %s" % {i: FORASTEROS[i] for i in mal}
print("guarda anti-secuestro OK: 0 ids forasteros (%d vigilados)" % len(FORASTEROS))

GRANDES = ["A Coruña", "Santiago de Compostela", "Ferrol", "Narón", "Oleiros",
           "Arteixo", "Culleredo", "Ames", "Carballo", "Ribeira"]
for g in GRANDES:
    assert g in mapa, g
print("grandes OK:", ", ".join("%s=%s" % (g, mapa[g]) for g in GRANDES))

os.makedirs("ordenanzas_data", exist_ok=True)
json.dump(mapa, open("ordenanzas_data/bop_acoruna_municipios.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1, sort_keys=True)
cfg = {"id": "acoruna", "base": "https://bop.dacoruna.gal",
       "mapa": "bop_acoruna_municipios.json", "nombre": "A Coruña",
       "familia": "acoruna", "indice_desde": 2009}
json.dump(cfg, open("ordenanzas_data/bop_acoruna_config.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("\nescritos:\n  ordenanzas_data/bop_acoruna_municipios.json (%d)\n  ordenanzas_data/bop_acoruna_config.json" % len(mapa))
print(json.dumps(cfg, ensure_ascii=False))
