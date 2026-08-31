# -*- coding: utf-8 -*-
"""
Motor CONVENIOS COLECTIVOS — registro oficial REGCON del Ministerio de Trabajo
(expinterweb.mites.gob.es/regcon), que es el registro unico de convenios y
acuerdos colectivos de Espana: estatales, autonomicos, provinciales y de empresa.

POR QUE HAY INDICE EMPAQUETADO
------------------------------
REGCON es una aplicacion Java con sesion (JSESSIONID) y formularios POST: una
consulta cuesta 2 viajes (GET del formulario + POST de busqueda) y ~0,9 s desde
fuera. Para cumplir el requisito de "el convenio correcto en menos de 2
segundos" SIEMPRE, la identificacion del convenio se resuelve contra un indice
EMPAQUETADO en el repo (convenios_data/convenios.json): denominacion, ambito
territorial, codigo de convenio y URL oficial del ultimo texto publicado. Buscar
ahi no toca la red: ~10-30 ms.

REGCON en vivo se usa solo para lo que el indice no puede dar:
  * buscar DENTRO del texto de los convenios ("que convenios regulan el plus de
    nocturnidad en Madrid") -> buscador de textos, ~0,5 s;
  * convenios de EMPRESA que no esten en el indice;
  * comprobar el estado de vigencia y los ultimos tramites de un convenio.

TRAMPAS CONOCIDAS (verificadas 2026-08-20)
------------------------------------------
  * La pagina mezcla codificaciones: la plantilla estatica va en ISO-8859-1 y
    los datos dinamicos en UTF-8. Se decodifica en UTF-8 con errors="replace".
  * Una busqueda con denominacion VACIA en consultaPublica revienta la
    aplicacion ("Codigo de ERROR"); siempre hay que mandar texto.
  * El contador de resultados usa &nbsp;, no espacios: "Resultados1 - 10&nbsp;de
    &nbsp;218". Hay que des-escapar el HTML antes de aplicar la expresion.
  * El buscador de textos devuelve UNA FILA POR PUBLICACION, no por convenio: el
    mismo codigo aparece tantas veces como veces se publico su texto, ordenadas
    de mas reciente a mas antigua.
"""
import os
import re
import time
import json
import html as _html
import unicodedata
import urllib.parse as _up

import httpx

BASE = "https://expinterweb.mites.gob.es/regcon/pub/"
URL_TEXTOS = BASE + "buscadorTextosEstatal"
URL_CONSULTA = BASE + "consultaPublica"
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}

SALTO = chr(10)

_DATOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "convenios_data")

try:
    from convenios_territorios import (AUTORIDADES, COMUNIDAD_PROVINCIAS,
                                       PROVINCIA_COMUNIDAD, UNIPROVINCIALES, ALIAS)
except Exception:  # pragma: no cover - el conector debe seguir vivo sin esto
    AUTORIDADES, COMUNIDAD_PROVINCIAS, PROVINCIA_COMUNIDAD = {}, {}, {}
    UNIPROVINCIALES, ALIAS = set(), {}

_AMBITOS = {"6": "sector (provincial o superior)", "5": "sector local/comarcal",
            "4": "grupo de empresas", "3": "empresa", "2": "centros de trabajo",
            "1": "franja"}

# Palabras vacias: no aportan nada al identificar un sector.
_STOP = {
    "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "al", "a",
    "y", "e", "o", "u", "en", "por", "para", "con", "sin", "sobre", "que", "cual",
    "cuales", "es", "son", "se", "su", "sus", "me", "mi", "lo", "como", "cuanto",
    "convenio", "convenios", "colectivo", "colectivos", "aplicable", "aplica",
    "vigente", "actual", "busca", "buscar", "buscame", "dame", "quiero",
    "necesito", "encuentra", "encuentrame", "dime", "cual", "sector", "trabajo",
    "trabajadores", "trabajador", "empresa", "empresas", "provincia",
    "provincial", "comunidad", "comunitat", "autonoma", "autonomo", "region",
    "ambito", "territorio", "texto", "todo", "toda", "por favor", "porfavor",
}

# Sinonimos de sector: lo que dice el abogado -> como se llama en el registro.
_SINONIMOS = {
    "hosteleria": ["hosteleria", "hostelera", "turisticas", "restauracion",
                   "bares", "cafeterias", "restaurantes"],
    "hosteleros": ["hosteleria"],
    "restauracion": ["hosteleria", "restauracion", "colectividades"],
    "bar": ["hosteleria", "bares"], "bares": ["hosteleria", "bares"],
    "hotel": ["hosteleria", "hoteles"], "hoteles": ["hosteleria", "hoteles"],
    "metal": ["metal", "siderometalurgia", "metalurgica", "metalgrafica",
              "siderometalurgica"],
    "metalurgia": ["metal", "siderometalurgia"],
    "siderometalurgia": ["siderometalurgica", "metal"],
    "siderurgia": ["siderometalurgica", "metal"],
    "calzado": ["calzado", "piel"],
    "graficas": ["graficas", "artes"],
    "dependientes": ["dependientes", "dependencia"],
    "panaderias": ["panaderias", "panaderia", "obradores"],
    "construccion": ["construccion", "edificacion", "obras", "albanileria"],
    "obra": ["construccion"], "albanil": ["construccion"],
    "limpieza": ["limpieza", "limpiezas"],
    "oficinas": ["oficinas", "despachos"], "despachos": ["oficinas", "despachos"],
    "comercio": ["comercio", "comercial", "detallistas", "mayoristas"],
    "transporte": ["transporte", "transportes", "mercancias", "viajeros"],
    "sanidad": ["sanidad", "sanitaria", "clinicas", "hospitales", "sanitario"],
    "clinicas": ["clinicas", "sanidad"], "hospitales": ["hospitales", "sanidad"],
    "ensenanza": ["ensenanza", "educacion", "colegios", "docente"],
    "educacion": ["ensenanza", "educacion"],
    "colegios": ["ensenanza", "colegios"],
    "agricultura": ["agricola", "agropecuario", "campo", "agrario"],
    "campo": ["campo", "agricola", "agropecuario"],
    "peluquerias": ["peluquerias", "belleza", "esteticas"],
    "estetica": ["esteticas", "belleza", "peluquerias"],
    "seguridad": ["seguridad", "vigilancia"],
    "dependencia": ["dependientes", "dependencia", "residencias", "geriatricos"],
    "residencias": ["residencias", "geriatricos", "dependientes"],
    "geriatrico": ["residencias", "geriatricos"],
    "ayuda": ["ayuda", "domicilio"],
    "supermercados": ["supermercados", "alimentacion", "detallistas"],
    "alimentacion": ["alimentacion", "detallistas"],
    "grandes almacenes": ["almacenes"],
    "artes graficas": ["graficas", "artes"],
    "quimica": ["quimica", "quimicas"],
    "textil": ["textil", "confeccion"],
    "madera": ["madera", "maderas", "carpinteria", "mueble"],
    "panaderia": ["panaderias", "panaderia", "obradores"],
    "carniceria": ["carnicas", "carnicerias", "carne"],
    "oficina": ["oficinas", "despachos"],
    "banca": ["banca", "bancos", "ahorro"],
    "seguros": ["seguros", "reaseguros", "mutuas"],
    "call center": ["contact", "telemarketing"],
    "telemarketing": ["contact", "telemarketing"],
    "informatica": ["informatica", "consultoras", "tecnologias"],
    "consultoria": ["consultoras", "consultoria", "ingenierias"],
    "gestorias": ["gestorias", "oficinas", "despachos"],
    "abogados": ["despachos", "oficinas"],
    "farmacia": ["farmacia", "oficinas de farmacia"],
    "jardineria": ["jardineria", "jardines"],
    "aparcamientos": ["aparcamientos", "garajes", "estacionamientos"],
    "garajes": ["garajes", "aparcamientos"],
    "ocio": ["ocio", "recreativas"],
    "deporte": ["deportivas", "instalaciones deportivas", "gimnasios"],
    "gimnasios": ["gimnasios", "deportivas"],
    "camareras de piso": ["hosteleria", "hoteles"],
    "autoescuelas": ["autoescuelas", "ensenanza"],
    "funerarias": ["funerarias", "pompas"],
    "taxi": ["auto-taxi", "taxi", "autotaxi"],
}



# Palabras de la denominacion que no distinguen un sector de otro.
_RUIDO_DENOM = {"provincia", "provincial", "comunidad", "comunitat", "autonoma",
                "sector", "sectorial", "convenio", "colectivo", "col", "lectiu",
                "conveni", "treball", "trabajo", "empresas", "empresa", "general",
                "unico", "estatal", "nacional", "ambito", "para", "ano", "anos",
                "i", "el", "la", "les", "dels", "del", "de", "per", "als", "any",
                "activitats", "actividades", "servicios", "serveis", "provincies"}

# Todos los tokens que son territorio (Madrid, Barcelona, Alacant...): en una
# denominacion no informan del sector, ya los ha filtrado el ambito territorial.
_ALIAS_TOKENS = set()
for _a in ALIAS:
    _ALIAS_TOKENS.update(_a.split())


# ---------------------------------------------------------------- utilidades

def _norm(s: str) -> str:
    """minusculas, sin tildes, sin puntuacion: para comparar como comparan las personas."""
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.lower().replace("/", " ").replace("-", " ")
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _tokens(s: str):
    return [t for t in _norm(s).split() if len(t) > 1 and t not in _STOP]


def _limpiar_html(fragmento: str) -> str:
    txt = re.sub(r"<(br|/p|/div|/li|/tr)[^>]*>", "\n", fragmento or "", flags=re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    return re.sub(r"[ \t]+", " ", _html.unescape(txt).replace("\xa0", " ")).strip()


# ------------------------------------------------------- indice empaquetado

_INDICE = None


def _cargar():
    """Carga (una vez) el indice empaquetado. Devuelve [] si no esta."""
    global _INDICE
    if _INDICE is not None:
        return _INDICE
    ruta = os.path.join(_DATOS, "convenios.json")
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            crudo = json.load(f)
        _INDICE = []
        for c in crudo.get("conv", []):
            # el 6o campo es la denominacion ya normalizada: normalizar 10.500
            # titulos en cada arranque en frio se comia 0,4 s del margen de 2 s.
            n = c[5] if len(c) > 5 else _norm(c[1])
            _INDICE.append({
                "codigo": c[0], "denominacion": c[1], "al": c[2], "amb": c[3],
                "url": c[4], "_n": n, "_tok": set(n.split()),
            })
    except Exception:  # noqa: BLE001
        _INDICE = []
    return _INDICE


# ------------------------------------------------------ territorio (parsing)

_ALIAS_ORD = None


def _alias_ordenados():
    global _ALIAS_ORD
    if _ALIAS_ORD is None:
        _ALIAS_ORD = sorted(ALIAS.items(), key=lambda kv: -len(kv[0]))
    return _ALIAS_ORD


def detectar_territorio(texto: str):
    """('28', 'Madrid', resto_sin_el_territorio) o (None, '', texto_normalizado).

    Coincide por palabra completa y del alias mas largo al mas corto, para que
    'las palmas' gane a 'palmas' y 'castilla y leon' gane a 'leon'.
    """
    n = _norm(texto)
    if not n:
        return None, "", ""
    for alias, aid in _alias_ordenados():
        if re.search(r"(?:^|\s)" + re.escape(alias) + r"(?:\s|$)", n):
            resto = re.sub(r"(?:^|\s)" + re.escape(alias) + r"(?=\s|$)", " ", n).strip()
            return aid, AUTORIDADES.get(aid, ("", ""))[0], re.sub(r"\s+", " ", resto)
    return None, "", n


def _ambito_territorial(aid: str):
    """Autoridades a consultar cuando el usuario nombra un territorio.

    Quien pregunta por 'la Comunidad Valenciana' quiere tambien Valencia,
    Alicante y Castellon: ahi es donde se registran casi todos los convenios de
    sector. Quien pregunta por 'Alicante' agradece ver ademas el autonomico y el
    ESTATAL, porque son los que se le aplican si su provincia no tiene convenio
    propio del sector (es el caso real mas frecuente).

    Devuelve la lista ordenada por cercania: [pedido, ...relacionados, estatal].
    """
    if not aid:
        return []
    fuera = [aid]
    tipo = AUTORIDADES.get(aid, ("", "P"))[1]
    if tipo == "C":
        fuera += COMUNIDAD_PROVINCIAS.get(aid, [])
    elif tipo == "P":
        com = PROVINCIA_COMUNIDAD.get(aid)
        if com:
            fuera.append(com)
    if "63" not in fuera:
        fuera.append("63")
    return fuera


# --------------------------------------------------------------- puntuacion

# Sectores "cabecera": si la denominacion empieza por uno distinto del que se
# pregunta, es OTRO convenio ("comercio del metal" no es el convenio del metal).
_CABECERAS = {"comercio", "comerc", "transporte", "transportes",
              "fabricacion", "distribucion", "almacenes", "almacenistas",
              "mayoristas", "minoristas", "detallistas", "elaboracion",
              "ciclo", "rematantes", "aserraderos", "embotellado"}


def _puntuar(reg, toks_orig, frase, aids_pref):
    """Puntua un convenio del indice frente a lo que ha preguntado el usuario."""
    dt = reg["_tok"]

    # --- territorio: fuera de los ambitos pedidos, ni se mira ---
    terr = 0.0
    if aids_pref:
        if reg["al"] not in aids_pref:
            return 0.0
        pos = aids_pref.index(reg["al"])
        terr = 7.0 if pos == 0 else (3.2 if reg["al"] != "63" else 1.6)

    # --- sector ---
    if not toks_orig:
        base, cobertura = 1.0, 1.0
    else:
        base, cubiertos, gastados = 0.0, 0, set()
        for t in toks_orig:
            # Un sinonimo NO puede hacer que dos palabras distintas de la
            # pregunta se apunten la MISMA palabra del titulo: "oficinas y
            # despachos" casaria entero con "OFICINAS DE FARMACIA".
            variantes = [t] + _norm(" ".join(_SINONIMOS.get(t, []))).split()
            mejor, gastado = 0.0, None
            for v in variantes:
                if v in dt and v not in gastados:
                    p = 3.0 if v == t else 2.4
                    if p > mejor:
                        mejor, gastado = p, v
                elif len(v) >= 5:
                    for d in dt:
                        if d in gastados:
                            continue
                        if d.startswith(v[:5]) and (d.startswith(v) or v.startswith(d)):
                            p = 1.9 if v == t else 1.5
                            if p > mejor:
                                mejor, gastado = p, d
                            break
            if mejor:
                cubiertos += 1
                gastados.add(gastado)
            base += mejor
        cobertura = cubiertos / len(toks_orig)
        if cobertura < 0.5:
            return 0.0
        base *= (0.4 + 0.6 * cobertura)

    # la denominacion contiene la frase tal cual ("artes graficas")
    if frase and len(frase) > 6 and frase in reg["_n"]:
        base += 4.0

    if toks_orig:
        pedidos = set(toks_orig)
        for t in toks_orig:
            pedidos.update(_norm(" ".join(_SINONIMOS.get(t, []))).split())

        # Ante "comercio de Barcelona" gana el convenio del comercio, no el del
        # comercio textil: cuanto menos ruido sobre lo preguntado, mejor. Suave y
        # con tope, porque muchos titulos OFICIALES son largos de por si
        # ("ARTES GRAFICAS. MANIPULADO PAPEL, MANIPULADOS DE CARTON...").
        sobra = [d for d in dt
                 if d not in pedidos and d not in _STOP and d not in _RUIDO_DENOM
                 and len(d) > 2 and d not in _ALIAS_TOKENS]
        base -= min(len(sobra), 5) * 0.30

        # "COMERCIO DE CURTIDOS Y ARTICULOS PARA EL CALZADO" no es el convenio
        # del calzado, es uno de comercio: si el titulo se encabeza con OTRO
        # sector antes de la palabra preguntada, baja.
        palabras = reg["_n"].split()
        pos_match = next((i for i, w in enumerate(palabras) if w in pedidos), -1)
        if pos_match > 0 and any(w in _CABECERAS and w not in pedidos
                                 for w in palabras[:pos_match]):
            base -= 2.2

    # un convenio de sector es lo que se pregunta el 95% de las veces
    base += 1.0 if reg["amb"] == "6" else (0.4 if reg["amb"] == "5" else 0.0)
    return base + terr if base > 0 else 0.0


# ------------------------------------------------------------ REGCON en vivo

# Los boletines oficiales espanoles (bizkaia.eus, bopcadiz.es, deputacionlugo.gal,
# el propio REGCON...) usan CA del sector publico que no estan en el bundle de
# certifi: verificar el certificado tumbaba la descarga con
# CERTIFICATE_VERIFY_FAILED. Son sitios publicos de SOLO LECTURA y no se les
# manda ningun dato, asi que se desactiva la verificacion, igual que en
# teac_engine y dgt_engine. Se puede reactivar con CONVENIOS_TLS_VERIFY=1.
_VERIFY = os.environ.get("CONVENIOS_TLS_VERIFY", "0") != "0"


def _cliente(timeout=25):
    return httpx.Client(headers=_UA, timeout=timeout, follow_redirects=True,
                        verify=_VERIFY)


# REGCON es una aplicacion del sector publico que parpadea: timeouts, cortes de
# sesion (JSESSIONID) y 5xx esporadicos. Un fallo puntual NO debe llegar al
# usuario como "no hay conexion con REGCON": se reintenta con sesion nueva y una
# espera corta creciente antes de rendirse. Solo reintenta fallos de red/httpx;
# los errores de logica (parseo, etc.) caen a la primera sin gastar reintentos.
def _con_reintento(fn, *args, intentos: int = 3, espera: float = 0.7, **kw):
    ultimo = None
    for i in range(intentos):
        try:
            return fn(*args, **kw)
        except httpx.HTTPError as e:  # noqa: BLE001
            ultimo = e
            if i + 1 < intentos:
                time.sleep(espera * (i + 1))
    raise ultimo


def _filas_textos(h: str):
    """Filas de la tabla del buscador de textos (una por PUBLICACION)."""
    m = re.search(r'<table[^>]*summary="Acuerdos".*?</table>', h, re.S | re.I)
    if not m:
        return []
    fuera = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(0), re.S | re.I)[1:]:
        celdas = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.S | re.I)
        if len(celdas) < 5:
            continue
        v = [_limpiar_html(c) for c in celdas]
        u = re.search(r'href="([^"]+)"', celdas[-1])
        fuera.append({"codigo": v[0], "denominacion": v[1], "naturaleza": v[2],
                      "autoridad": v[3], "estado": v[4] if len(v) > 4 else "",
                      "url": _html.unescape(u.group(1)) if u else ""})
    return fuera


def _total_textos(h: str) -> int:
    t = _html.unescape(re.sub("<[^>]+>", " ", h)).replace("\xa0", " ")
    m = re.search(r"Resultados\s*\d+\s*-\s*\d+\s*de\s*([\d\.]+)", t)
    return int(m.group(1).replace(".", "")) if m else 0


def buscar_en_texto(texto: str, aid: str = "", ambito: str = "6",
                    naturaleza: str = "1", maximo: int = 10, paginas: int = 1,
                    timeout: int = 25):
    """Busca DENTRO del texto integro de los convenios (REGCON en vivo)."""
    def _una_vez():
        with _cliente(timeout) as c:
            c.get(URL_TEXTOS)
            datos = {"texto": texto, "coincidencia": "1", "idNaturaleza": naturaleza,
                     "_esNuevaBusqueda": "1", "_buscar": ""}
            if aid:
                datos["idAutoridadLaboral"] = aid
            if ambito:
                datos["idAmbitoFuncional"] = ambito
            r = c.post(URL_TEXTOS, data=datos)
            filas = _filas_textos(r.text)
            total = _total_textos(r.text)
            p = 2
            while len(filas) < maximo and p <= paginas and len(filas) < total:
                rp = c.get(URL_TEXTOS, params={"pagina": str(p)})
                nuevas = _filas_textos(rp.text)
                if not nuevas:
                    break
                filas.extend(nuevas)
                p += 1
            return filas[:maximo], total
    return _con_reintento(_una_vez)


def _tramites(codigo: str, maximo: int = 12):
    """Historial de tramites de un convenio (consultaPublica): da la VIGENCIA."""
    def _una_vez():
        with _cliente() as c:
            c.get(URL_CONSULTA)
            r = c.post(URL_CONSULTA, data={
                "codigoConvenio": codigo, "denominacion": "",
                "tipoBusquedaDenominacion": "AND", "_esNuevaBusqueda": "1", "_buscar": ""})
            m = re.search(r'<table[^>]*summary="Tramites".*?</table>', r.text, re.S | re.I)
            if not m:
                return []
            fuera = []
            for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(0), re.S | re.I)[1:]:
                celdas = [_limpiar_html(x) for x in
                          re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.S | re.I)]
                if len(celdas) < 7:
                    continue
                fuera.append({"codigo": celdas[0], "denominacion": celdas[1],
                              "tramite": celdas[2], "autoridad": celdas[3],
                              "fecha": celdas[4], "desde": celdas[5], "hasta": celdas[6]})
            return fuera[:maximo]
    return _con_reintento(_una_vez)


# ------------------------------------------------------------ API: BUSCAR

def _etiqueta(reg) -> str:
    nom, tipo = AUTORIDADES.get(reg.get("al", ""), ("?", "P"))
    if tipo == "E":
        return "estatal"
    if reg["al"] in UNIPROVINCIALES:
        return nom
    return nom + (" (comunidad)" if tipo == "C" else " (provincia)")


def _linea(reg, n) -> str:
    partes = [f"{n}. {reg['denominacion']}",
              f"   Ambito: {_etiqueta(reg)} - {_AMBITOS.get(reg['amb'], '')}",
              f"   Codigo de convenio: {reg['codigo']}"]
    if reg.get("url"):
        partes.append(f"   Texto oficial: {reg['url']}")
    return "\n".join(partes)


def _es_si(v) -> bool:
    return str(v).strip().lower() in ("1", "si", "sí", "true", "yes", "y")


def buscar(consulta: str = "", territorio: str = "", ambito: str = "sector",
           en_texto: str = "", maximo: int = 8) -> str:
    """Localiza el convenio colectivo aplicable. Ver la tool `buscar_convenio`."""
    consulta = (consulta or "").strip()
    territorio = (territorio or "").strip()
    maximo = max(1, min(int(maximo or 8), 25))
    ambito = (ambito or "sector").strip().lower()

    # 1) territorio: el explicito manda; si no, se saca de la propia pregunta.
    if territorio:
        aid, _nom, _r = detectar_territorio(territorio)
        resto = _norm(consulta) if aid else None
    else:
        aid = None
    if not territorio or not aid:
        aid2, _nom2, resto2 = detectar_territorio(consulta)
        if not aid:
            aid, resto = aid2, resto2
    aids = _ambito_territorial(aid) if aid else []

    # 2) busqueda DENTRO del texto integro de los convenios (REGCON en vivo)
    if _es_si(en_texto):
        return _buscar_en_texto_fmt(consulta, aid, ambito, maximo)

    toks = [t for t in (resto or "").split() if t and t not in _STOP and len(t) > 1]
    frase = " ".join(toks)

    ambitos_ok = {"6", "5"}
    if ambito.startswith("empres"):
        ambitos_ok = {"3", "2", "4"}
    elif ambito in ("todos", "todo", "todas", ""):
        ambitos_ok = {"1", "2", "3", "4", "5", "6"}

    idx = _cargar()
    if not idx:
        return _buscar_en_texto_fmt(consulta, aid, ambito, maximo,
                                    aviso="[indice local no disponible: consulta en vivo]")

    puntuados = []
    for reg in idx:
        if reg["amb"] not in ambitos_ok:
            continue
        p = _puntuar(reg, toks, frase, aids)
        if p > 0:
            puntuados.append((p, reg))
    puntuados.sort(key=lambda x: -x[0])

    # Un mismo convenio consta con varias denominaciones historicas: uno por codigo.
    vistos, top = set(), []
    for p, reg in puntuados:
        if reg["codigo"] in vistos:
            continue
        vistos.add(reg["codigo"])
        top.append(reg)
        if len(top) >= maximo:
            break

    if not top:
        return _sin_resultados(consulta, aid, maximo, toks, frase, ambitos_ok)

    cab = ["CONVENIOS COLECTIVOS - registro oficial REGCON (Ministerio de Trabajo)"]
    if aid:
        extra = f"  (+{len(aids) - 1} ambito(s) relacionados)" if len(aids) > 1 else ""
        cab.append(f"Territorio: {AUTORIDADES.get(aid, ('?',))[0]}{extra}")
    if toks:
        cab.append("Sector buscado: " + " ".join(toks))
    cab.append(f"{len(top)} resultado(s), el mas ajustado primero:\n")
    cuerpo = "\n\n".join(_linea(reg, i) for i, reg in enumerate(top, 1))
    pie = ("\n\nPara el TEXTO del convenio: leer_convenio(\"" + top[0]["codigo"] + "\") "
           "(o con articulo=\"N\" / buscar_en=\"materia\"). Para saber QUE convenios "
           "regulan una materia concreta: buscar_convenio(..., en_texto=\"si\"). "
           "Para el estado de vigencia y sus tramites: vigencia_convenio(codigo).")
    return "\n".join(cab) + cuerpo + pie


def _fmt_filas(filas):
    return "\n\n".join(
        f"{i}. {f['denominacion']}\n   Ambito: {f['autoridad']}\n"
        f"   Codigo de convenio: {f['codigo']}"
        + (f"\n   Texto oficial: {f['url']}" if f["url"] else "")
        for i, f in enumerate(filas, 1))


def _buscar_en_texto_fmt(consulta, aid, ambito, maximo, aviso=""):
    amb = "6" if ambito in ("sector", "", None) else ("3" if ambito.startswith("empres") else "")
    try:
        filas, total = buscar_en_texto(consulta, aid or "", amb, "1", maximo, paginas=3)
    except Exception:  # noqa: BLE001
        return ("El registro REGCON del Ministerio de Trabajo no responde en este "
                "momento: es un corte temporal de su servidor, no del conector. "
                "Vuelve a intentarlo en unos segundos.")
    if not filas:
        donde = f" en {AUTORIDADES.get(aid, ('?',))[0]}" if aid else ""
        return (f"Ningun convenio{donde} contiene {consulta!r} en su texto. "
                "Prueba con menos palabras, o sin filtrar territorio.")
    cab = ([aviso] if aviso else []) + ["CONVENIOS CUYO TEXTO CONTIENE: " + consulta]
    if aid:
        cab.append("Territorio: " + AUTORIDADES.get(aid, ("?",))[0])
    cab.append(f"{len(filas)} de {total} coincidencia(s) - busqueda a texto integro "
               "en el registro oficial REGCON:\n")
    return "\n".join(cab) + _fmt_filas(filas)


def _sin_resultados(consulta, aid, maximo, toks, frase, ambitos_ok):
    """Que hacer cuando en ese territorio no hay convenio de ese sector.

    Lo primero es RELAJAR EL TERRITORIO contra el indice local (instantaneo):
    lo normal es que la provincia no tenga convenio propio y se aplique el
    estatal o el de otro ambito, y eso es justo lo que el abogado necesita
    saber. Solo si tampoco hay nada se pregunta al registro en vivo, y con
    presupuesto de tiempo para no pasarse de los 2 s.
    """
    idx = _cargar()
    sueltos = []
    for reg in idx:
        if reg["amb"] not in ambitos_ok:
            continue
        p = _puntuar(reg, toks, frase, [])
        if p > 0:
            sueltos.append((p, reg))
    sueltos.sort(key=lambda x: -x[0])
    vistos, top = set(), []
    for p, reg in sueltos:
        if reg["codigo"] in vistos:
            continue
        vistos.add(reg["codigo"])
        top.append(reg)
        if len(top) >= maximo:
            break
    if top:
        donde = AUTORIDADES.get(aid, ("ese territorio",))[0] if aid else "ese territorio"
        cab = ["No hay convenio propio de " + repr(consulta.strip()) +
               " registrado en " + donde + ".",
               "Cuando eso pasa se aplica el de ambito superior (estatal o autonomico)",
               "o, en su defecto, el del sector afin. Convenios de ese sector en el",
               "resto del registro, del mas ajustado al menos:", ""]
        return SALTO.join(cab) + (SALTO + SALTO).join(
            _linea(r, i) for i, r in enumerate(top, 1))

    try:
        filas, _total = buscar_en_texto(consulta, aid or "", "", "1", maximo,
                                        paginas=1, timeout=6)
    except Exception:  # noqa: BLE001
        filas = []
    if filas:
        return ("No hay convenio de SECTOR con ese nombre en el indice; esto es lo "
                "que devuelve el registro en vivo:" + SALTO + SALTO + _fmt_filas(filas))
    donde = (" en " + AUTORIDADES.get(aid, ("?",))[0]) if aid else ""
    return ("No consta ningun convenio colectivo de " + repr(consulta.strip()) + donde +
            " en el registro REGCON del Ministerio de Trabajo. Prueba con el nombre del "
            "sector tal y como lo usa el registro (p.ej. 'siderometalurgia' en vez de "
            "'metal', 'hosteleria' en vez de 'bares'), o quita el territorio para ver si "
            "existe un convenio estatal del sector.")


# -------------------------------------------------------------- API: LEER

def _extraer_pdf(datos: bytes, desde_pagina: int = 0) -> str:
    """Texto del PDF. `desde_pagina` (0-based) atiende al ancla #page=N: en los
    boletines provinciales la URL apunta a la pagina donde empieza el convenio
    dentro del boletin del dia, que trae decenas de anuncios mas."""
    try:
        import fitz  # PyMuPDF: ~10x mas rapido que pypdf
        with fitz.open(stream=datos, filetype="pdf") as doc:
            ini = desde_pagina if 0 <= desde_pagina < doc.page_count else 0
            return SALTO.join(doc[i].get_text() for i in range(ini, doc.page_count))
    except Exception:  # noqa: BLE001
        pass
    try:
        import io as _io
        from pypdf import PdfReader
        paginas = PdfReader(_io.BytesIO(datos)).pages
        ini = desde_pagina if 0 <= desde_pagina < len(paginas) else 0
        return SALTO.join((p.extract_text() or "") for p in paginas[ini:])
    except Exception:  # noqa: BLE001
        return ""


def _descargar_texto(url: str, timeout: int = 25) -> str:
    # 517 de las 10.551 URLs traen ancla "#page=N": la pagina del boletin donde
    # empieza el convenio. El fragmento no viaja en la peticion, pero si dice
    # por donde hay que empezar a leer.
    pagina = 0
    m = re.search(r"#page=(\d+)", url)
    if m:
        pagina = max(0, int(m.group(1)) - 1)
        url = url.split("#", 1)[0]
    with _cliente(timeout) as c:
        r = c.get(url)
        r.raise_for_status()
        tipo = (r.headers.get("content-type") or "").lower()
        if "pdf" in tipo or r.content[:4] == b"%PDF":
            return _extraer_pdf(r.content, pagina)
        h = re.sub(r"<(script|style|nav|header|footer).*?</\1>", " ", r.text,
                   flags=re.S | re.I)
        texto = _limpiar_html(h)
        # Varios BOP (Barcelona, Tarragona, Lugo...) no sirven el PDF en la URL
        # registrada sino una pagina de anuncio que lo enlaza: si lo que hemos
        # sacado es poco mas que el menu de la web, se sigue ese enlace UNA vez.
        if len(texto) < 4000:
            pdf = _enlace_pdf(r.text, str(r.url))
            if pdf:
                try:
                    return _descargar_texto(pdf, timeout)
                except Exception:  # noqa: BLE001
                    pass
        return texto



# Enlaces que un boletin usa para el PDF de un anuncio, del mas fiable al menos.
_PATRONES_PDF = (
    r"descarrega-pdf[^\"']*", r"descarga[^\"']*pdf[^\"']*", r"download[^\"']*pdf[^\"']*",
    r"[^\"']+\.pdf(?:#page=\d+)?",
)


def _enlace_pdf(html: str, base_url: str):
    """Primer enlace de la pagina que parezca el PDF del anuncio, absoluto."""
    enlaces = re.findall(r"(?:href|src|data)=[\"']([^\"']{4,300})[\"']", html, re.I)
    for patron in _PATRONES_PDF:
        for e in enlaces:
            if re.fullmatch(patron, e, re.I) or re.search(patron, e, re.I):
                # "veure-pdf" es el visor JS, no el fichero: no sirve.
                if "veure-pdf" in e.lower() or "ver-pdf" in e.lower():
                    continue
                return _up.urljoin(base_url, _html.unescape(e))
    return None


_RE_ART = re.compile(
    r"(?im)^[ \t]*(?:art[íi]culo|art\.?)\s*([0-9]{1,3})\s*[ºo]?\s*[\.\-:]?\s*(.{0,110})$")


def _indice_articulos(texto: str):
    return [(m.group(1), m.group(2).strip(" .-:"), m.start()) for m in _RE_ART.finditer(texto)]


def _resolver(codigo: str, consulta: str, territorio: str):
    idx = _cargar()
    if codigo:
        for r in idx:
            if r["codigo"] == codigo:
                return r
    if consulta or territorio:
        if territorio:
            aid, _n, _x = detectar_territorio(territorio)
            resto = _norm(consulta)
        else:
            aid, _n, resto = detectar_territorio(consulta)
        aids = _ambito_territorial(aid) if aid else []
        toks = [t for t in resto.split() if t and t not in _STOP and len(t) > 1]
        mejor, mejorp = None, 0.0
        for r in idx:
            p = _puntuar(r, toks, " ".join(toks), aids)
            if p > mejorp:
                mejor, mejorp = r, p
        if mejor:
            return mejor
    if codigo:
        # no esta en el indice empaquetado: se pregunta al registro en vivo
        try:
            tr = _tramites(codigo, 1)
            if tr:
                filas, _ = buscar_en_texto(tr[0]["denominacion"][:60], "", "", "1", 8)
                for f in filas:
                    if f["codigo"] == codigo and f["url"]:
                        return {"codigo": codigo, "denominacion": f["denominacion"],
                                "al": "", "amb": "", "url": f["url"]}
        except Exception:  # noqa: BLE001
            pass
    return None


def leer(codigo: str = "", consulta: str = "", territorio: str = "",
         articulo: str = "", buscar_en: str = "", max_chars: int = 45000) -> str:
    """Texto oficial de un convenio. Ver la tool `leer_convenio`."""
    reg = _resolver(re.sub(r"\D", "", codigo or ""), consulta, territorio)
    if reg is None:
        return ("No identifico ese convenio. Localizalo primero con "
                "buscar_convenio(\"<sector>\", territorio=\"<provincia o comunidad>\") "
                "y usa el codigo de convenio que te devuelva.")
    if not reg.get("url"):
        return (f"{reg['denominacion']} (codigo {reg['codigo']}) consta en el registro "
                "pero sin texto publicado accesible.")

    try:
        texto = _descargar_texto(reg["url"])
    except Exception as e:  # noqa: BLE001
        return (f"{reg['denominacion']} - codigo {reg['codigo']}\n"
                f"Texto oficial: {reg['url']}\n"
                f"(No se ha podido descargar el texto: {type(e).__name__}.)")
    texto = re.sub(r"\n{3,}", "\n\n", texto or "").strip()
    # Algunos BOP (Almeria, Ourense, Las Palmas, Tenerife) sirven un visor en
    # JavaScript: se descarga la pagina, pero no el convenio. Antes que colar el
    # menu de la web como si fuera el texto, se dice y se da el enlace oficial.
    if len(texto) < 1200:
        return (f"{reg['denominacion']} - codigo {reg['codigo']}" + SALTO
                + f"Texto oficial: {reg['url']}" + SALTO + SALTO
                + "El boletin que publica este convenio no sirve su texto de forma "
                  "legible automaticamente (visor en JavaScript o PDF sin capa de "
                  "texto). El enlace de arriba ES el texto oficial: abrelo para "
                  "leerlo. NO inventes su contenido ni lo des por conocido.")

    cab = [reg["denominacion"],
           f"Codigo de convenio: {reg['codigo']}"
           + (f"   |   Ambito: {_etiqueta(reg)}" if reg.get("al") else ""),
           f"Publicacion oficial: {reg['url']}", ""]

    if articulo:
        num = re.sub(r"\D", "", articulo)
        arts = _indice_articulos(texto)
        for i, (n, tit, pos) in enumerate(arts):
            if n == num:
                fin = arts[i + 1][2] if i + 1 < len(arts) else len(texto)
                cab.append(f"ARTICULO {n}" + (f" - {tit}" if tit else "") + "\n")
                return "\n".join(cab) + texto[pos:fin].strip()[:max_chars]
        cab.append(f"[No localizo un articulo {num}; se devuelve el indice del convenio]\n")
        arts_txt = "\n".join(f"  Art. {n}. {t}"[:110] for n, t, _ in arts[:150])
        return "\n".join(cab) + (arts_txt or texto[:max_chars])

    if buscar_en:
        pats = [p for p in _norm(buscar_en).split() if p not in _STOP]
        golpes, bajo = [], _norm_pos(texto)
        if pats:
            for m in re.finditer(re.escape(pats[0]), bajo):
                ini = max(0, m.start() - 400)
                golpes.append(texto[ini:m.start() + 900].strip())
                if len(golpes) >= 6:
                    break
        if golpes:
            cab.append(f"PASAJES SOBRE {buscar_en!r} ({len(golpes)}):\n")
            return "\n".join(cab) + ("\n\n[...]\n\n".join(golpes))[:max_chars]
        cab.append(f"[Sin pasajes sobre {buscar_en!r}; se devuelve el texto completo]\n")

    arts = _indice_articulos(texto)
    if arts:
        cab.append(f"({len(arts)} articulos detectados en el texto)\n")
    cola = ("\n\n[...texto truncado. Pide un articulo concreto con "
            "leer_convenio(codigo, articulo=\"N\") o busca dentro con "
            "buscar_en=\"materia\"]" if len(texto) > max_chars else "")
    return "\n".join(cab) + texto[:max_chars] + cola


def _norm_pos(s: str) -> str:
    """Como _norm pero SIN cambiar la longitud: los indices siguen valiendo."""
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.lower()


def vigencia(codigo: str) -> str:
    """Estado y tramites de un convenio (REGCON en vivo). Ver `vigencia_convenio`."""
    codigo = re.sub(r"\D", "", codigo or "")
    if not codigo:
        return "Indica el codigo de convenio (14 digitos) que te dio buscar_convenio."
    try:
        tr = _tramites(codigo, 15)
    except Exception:  # noqa: BLE001
        return ("El registro REGCON del Ministerio de Trabajo no responde en este "
                "momento: es un corte temporal de su servidor, no del conector. "
                "Vuelve a intentarlo en unos segundos.")
    if not tr:
        return f"El registro REGCON no devuelve tramites para el codigo {codigo}."
    cab = [tr[0]["denominacion"], f"Codigo de convenio: {codigo}",
           f"Autoridad laboral: {tr[0]['autoridad']}",
           f"Vigencia inscrita: {tr[0]['desde']} a {tr[0]['hasta']}", "",
           "TRAMITES REGISTRADOS (del mas reciente al mas antiguo):"]
    for t in tr:
        cab.append(f"  {t['fecha']}  {t['tramite']}  "
                   f"[vigencia {t['desde']} - {t['hasta']}]")
    return "\n".join(cab)
