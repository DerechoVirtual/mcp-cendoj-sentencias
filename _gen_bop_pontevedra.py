# -*- coding: utf-8 -*-
"""Genera el mapa de concellos de la provincia de PONTEVEDRA para el BOPPO.

El buscador del BOPPO (Liferay, portlet bopv2portlet) filtra por 'emisor' usando
el id HOJA (nivel 4 de la cascada), NO el id de la categoria del concello (nivel 3).
Este script recorre la cascada AJAX y escribe:
  ordenanzas_data/bop_pontevedra_municipios.json  {concello: "<id hoja>"}
Solo CONCELLOS de la provincia (fuera diputacion, mancomunidades, consorcios,
entidades locales menores, organismos autonomos y municipios de otras provincias).
"""
import html as H
import http.cookiejar, json, os, re, ssl, sys, time, unicodedata, urllib.parse, urllib.request
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ctx = ssl._create_unverified_context()
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120"
B = "https://boppo.depo.gal"
PAGE = B + "/buscas-no-boppo"
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "ordenanzas_data")

# 61 concellos de la provincia de Pontevedra (INE 2026). Clave = forma normalizada.
CONCELLOS = [
    "Agolada", "Arbo", "Baiona", "Barro", "Bueu", "Caldas de Reis", "Cambados",
    "Campo Lameiro", "Cangas", "A Cañiza", "Catoira", "Cerdedo-Cotobade", "Covelo",
    "Crecente", "Cuntis", "Dozón", "A Estrada", "Forcarei", "Fornelos de Montes",
    "Gondomar", "O Grove", "A Guarda", "A Illa de Arousa", "A Lama", "Lalín", "Marín",
    "Meaño", "Meis", "Moaña", "Mondariz", "Mondariz-Balneario", "Moraña", "Mos",
    "As Neves", "Nigrán", "Oia", "Pazos de Borbén", "Poio", "Ponte Caldelas",
    "Ponteareas", "Pontecesures", "Pontevedra", "O Porriño", "Portas", "Redondela",
    "Ribadumia", "Rodeiro", "O Rosal", "Salceda de Caselas", "Salvaterra de Miño",
    "Sanxenxo", "Silleda", "Soutomaior", "Tomiño", "Tui", "Valga", "Vigo",
    "Vila de Cruces", "Vilaboa", "Vilagarcía de Arousa", "Vilanova de Arousa",
]
# concellos historicos fusionados en Cerdedo-Cotobade (2016): utiles para recall antiguo
HISTORICOS = ["Cerdedo", "Cotobade"]

# el BOPPO nombra algunas entidades distinto que el INE
ALIAS = {
    "ocampolameiro": "Campo Lameiro",
    "cangasdemorrazo": "Cangas",
    "cerdedocotobade": "Cerdedo-Cotobade",
    "mondarizbalneario": "Mondariz-Balneario",
}


def norm(s):
    s = "".join(c for c in unicodedata.normalize("NFKD", (s or "").lower()) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s)


def canon(s):
    """'Caniza, A' -> 'acaniza'; 'Cangas De Morrazo' -> 'cangasdemorrazo'."""
    s = (s or "").strip()
    m = re.match(r"^(.*),\s*(A|O|As|Os|La|El)$", s, re.I)
    if m:
        s = m.group(2) + " " + m.group(1)
    return norm(s)


def ses():
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj),
                                     urllib.request.HTTPSHandler(context=ctx))
    op.addheaders = [("User-Agent", UA), ("Accept-Language", "gl-ES,gl;q=0.9,es;q=0.8")]
    page = op.open(PAGE, timeout=30).read().decode("utf-8", "replace")
    port = re.search(r"(buscadorbopv2portlet_WAR_bopv2portlet_INSTANCE_\w+)", page).group(1)
    return op, port


def ajax(op, port, nodo, valor):
    u = PAGE + "?" + urllib.parse.urlencode({
        "p_p_id": port, "p_p_lifecycle": "2", "p_p_state": "normal", "p_p_mode": "view",
        "p_p_resource_id": "ajaxCall", "p_p_cacheability": "cacheLevelPage",
        "p_p_col_id": "column-3", "p_p_col_pos": "1", "p_p_col_count": "2"})
    d = urllib.parse.urlencode({f"emisor{nodo}": valor, f"idEmisor{nodo+1}": "", "nodo": str(nodo)})
    h = op.open(urllib.request.Request(u, data=d.encode(), headers={
        "Referer": PAGE, "X-Requested-With": "XMLHttpRequest"}), timeout=30).read().decode("utf-8", "replace")
    return [(a, re.sub(r"\s+", " ", H.unescape(b)).strip())
            for a, b in re.findall(r'<option\s+value="([^"]+)"\s*>(.*?)</option>', h, re.S) if a != "-1"]


def main():
    op, port = ses()
    n2 = ajax(op, port, 1, "3")                      # ADMINISTRACION LOCAL
    mid = next(v for v, t in n2 if re.search(r"municipal", t, re.I))
    n3 = ajax(op, port, 2, mid)                      # entidades de nivel municipal
    print("nivel3:", len(n3), "entidades")
    json.dump(n3, open(os.path.join(HERE, "_pv_nivel3.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    quiero = {canon(c): c for c in CONCELLOS}
    quiero.update({canon(c): c for c in HISTORICOS})
    for k, v in ALIAS.items():
        quiero[k] = v
    mapa, hojas_raw, sin_hoja = {}, {}, []
    for cid, nombre in n3:
        c = canon(nombre)
        if c not in quiero:
            continue
        oficial = quiero[c]
        hijos = ajax(op, port, 3, cid)
        hojas_raw[nombre] = hijos
        # la hoja del propio concello: SOLO la que coincide en nombre con la entidad
        # (nunca un organismo autonomo/fundacion colgando del concello).
        cand = [h for h in hijos if canon(h[1]) in (c, canon(oficial))]
        if not cand:
            sin_hoja.append((nombre, cid, hijos))
            continue
        if oficial in mapa:                          # duplicado (p.ej. Sanxenxo x2)
            print("  ! duplicado ignorado:", oficial, cid, "->", cand[0])
            continue
        mapa[oficial] = cand[0][0]
        time.sleep(0.05)
    print("mapeados:", len(mapa), "| sin hoja:", sin_hoja)
    faltan = [c for c in CONCELLOS if c not in mapa]
    print("FALTAN:", faltan)
    json.dump(hojas_raw, open(os.path.join(HERE, "_pv_hojas_raw.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    salida = {k: mapa[k] for k in CONCELLOS if k in mapa}
    for h in HISTORICOS:
        if h in mapa:
            salida[h] = mapa[h]
    json.dump(salida, open(os.path.join(DATA, "bop_pontevedra_municipios.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("escrito bop_pontevedra_municipios.json con", len(salida), "entradas")


if __name__ == "__main__":
    main()
