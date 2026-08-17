# -*- coding: utf-8 -*-
"""
Motor DOCTRINA DE LA FISCALÍA GENERAL DEL ESTADO (FGE) — circulares, consultas
e instrucciones desde 1978 (fuente oficial: colección 'fiscalia' del BOE, que es
la base que la propia fiscal.es enlaza como acceso a su doctrina).

DISEÑO (por qué es instantáneo):
  * El corpus COMPLETO viaja EMPAQUETADO en el repo (fge_data/): catálogo de
    metadatos + índice invertido BM25 precomputado + texto íntegro por documento
    en gzip individual. La búsqueda NO toca la red: catálogo+índice se cargan
    una vez por contenedor (lazy, con precalentamiento en hilo al importar) y
    cada consulta se resuelve en memoria en milisegundos.
  * `leer` tampoco toca la red: descomprime solo el gzip del documento pedido.
  * El PDF oficial del BOE se enlaza siempre (abrir_fiscalia.php) como fuente.

La colección es CERRADA y pequeña (396 documentos, 1979-2026; la FGE emite unas
pocas piezas al año): se refresca re-ejecutando _gen_fge_data.py cuando haya
novedades, no en runtime.
"""
import gzip
import json
import math
import os
import re
import threading
import unicodedata
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_HERE, "fge_data")

# ------------------------------------------------------------------ carga lazy
_LOCK = threading.Lock()
_CAT = None      # lista de dicts del catálogo (más reciente primero)
_IDX = None      # {"N","avgdl","dl","txt","tit"}
_POR_REF = None  # ref -> posición en catálogo


def _cargar():
    global _CAT, _IDX, _POR_REF
    if _IDX is not None:
        return
    with _LOCK:
        if _IDX is not None:
            return
        cat = json.load(open(os.path.join(DATA_DIR, "catalogo.json"),
                             encoding="utf-8"))
        with gzip.open(os.path.join(DATA_DIR, "indice.json.gz"),
                       "rt", encoding="utf-8") as g:
            idx = json.load(g)
        _CAT, _IDX = cat, idx
        _POR_REF = {d["ref"]: i for i, d in enumerate(cat)}


def _precalentar():
    try:
        _cargar()
    except Exception:  # noqa: BLE001
        pass


# El primer arranque del contenedor carga el índice en segundo plano para que
# la primera búsqueda real no pague el cold start.
threading.Thread(target=_precalentar, daemon=True).start()


# ------------------------------------------------------- tokens (= generador)
_STOP = set("""a al algo ante como con contra cual cuales cuando de del desde donde dos el ella ellas ellos en entre era eran es esa esas ese esos esta estas este estos fue fueron ha haber habia han hasta hay la las le les lo los mas me mi mientras muy no nos nosotros o os otra otras otro otros para pero por porque que quien quienes se sea sean segun ser si sin sobre son su sus tal tambien tanto te tiene tienen toda todas todo todos tras tu un una unas uno unos vosotros y ya""".split())


def _quitar_tildes(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(c))


def _stem(t):
    if len(t) > 5 and t.endswith("mente"):
        t = t[:-5]
    for suf, rep in (("ciones", "cion"), ("siones", "sion"), ("idades", "idad"),
                     ("mientos", "miento"), ("amientos", "amiento")):
        if t.endswith(suf):
            return t[: -len(suf)] + rep
    if len(t) > 4 and t.endswith("es") and t[-3] not in "aeiou":
        return t[:-2]
    if len(t) > 3 and t.endswith("s") and t[-2] in "aeiou":
        return t[:-1]
    return t


def _tokens(s):
    s = _quitar_tildes((s or "").lower())
    out = []
    for t in re.findall(r"[a-z0-9]{2,}", s):
        if t in _STOP or t.isdigit() and len(t) > 4:
            continue
        out.append(_stem(t))
    return out


# Sinónimos jurídicos frecuentes -> términos con los que la FGE suele titular.
# Se AÑADEN a la consulta (no sustituyen), con peso menor en el scoring.
_SINONIMOS = {
    "violencia genero": ["violencia mujer", "violencia domestica"],
    "okupacion": ["usurpacion", "allanamiento morada"],
    "okupas": ["usurpacion", "allanamiento morada"],
    "menas": ["menores extranjeros no acompanados"],
    "alzamiento bienes": ["insolvencia punible"],
    "ciberdelito": ["criminalidad informatica"],
    "ciberdelincuencia": ["criminalidad informatica"],
    "cargar": [],
}


def _expandir(consulta):
    extra = []
    plano = " ".join(_tokens(consulta))
    for clave, adds in _SINONIMOS.items():
        if " ".join(_tokens(clave)) in plano:
            extra.extend(adds)
    return extra


# ----------------------------------------------------------------- utilidades
_TIPOS = {"C": "Circular", "I": "Instrucción", "Q": "Consulta"}
_TIPO_LETRA = {"circular": "C", "circulares": "C", "instruccion": "I",
               "instrucciones": "I", "consulta": "Q", "consultas": "Q"}

_RE_CITA = re.compile(
    r"\b(circular(?:es)?|instruccion(?:es)?|consultas?)\s+(?:n[ºo.]*\s*)?"
    r"(\d{1,2})\s*/\s*(\d{2,4})", re.I)
_RE_REF = re.compile(r"\bFIS-([CIQ])-(\d{4})-(\d{5})\b", re.I)


def _detectar_cita(texto):
    """'Circular 1/2023' / 'FIS-C-2023-00001' -> ref normalizada o None."""
    t = _quitar_tildes(texto or "")
    m = _RE_REF.search(t)
    if m:
        return f"FIS-{m.group(1).upper()}-{m.group(2)}-{m.group(3)}"
    m = _RE_CITA.search(t)
    if m:
        letra = _TIPO_LETRA.get(_quitar_tildes(m.group(1).lower()))
        num, anno = int(m.group(2)), int(m.group(3))
        if anno < 100:
            anno += 1900 if anno > 40 else 2000
        if letra:
            return f"FIS-{letra}-{anno}-{num:05d}"
    return None


def _fecha_bonita(d):
    if not d.get("fecha"):
        return str(d["anno"])
    a, m, dd = d["fecha"].split("-")
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
             "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    return f"{int(dd)} de {meses[int(m)-1]} de {a}"


def _texto_de(ref):
    fp = os.path.join(DATA_DIR, "textos", ref + ".json.gz")
    with gzip.open(fp, "rt", encoding="utf-8") as g:
        return json.load(g)["texto"]


def _pdf(ref):
    return f"https://www.boe.es/buscar/abrir_fiscalia.php?id={ref}.pdf"


def _cita_corta(d):
    return f"{d['tipo']} {d['numero']}/{d['anno']}"


def _snippet(texto, terminos, ancho=320):
    """Pasaje más relevante del texto para los términos dados. Barato: compara
    por substring sobre el párrafo normalizado (los stems españoles son
    prefijos), sin tokenizar el documento entero."""
    if not texto:
        return ""
    parrafos = texto.split("\n\n")
    objetivo = [t for t in set(terminos) if len(t) >= 3]
    mejor, mejor_score = None, 0
    for p in parrafos:
        if len(p) < 60:
            continue
        pn = _quitar_tildes(p.lower())
        score = sum(pn.count(t) for t in objetivo)
        if score > mejor_score:
            mejor, mejor_score = p, score
    if not mejor:
        # primer párrafo sustancial
        mejor = next((p for p in parrafos if len(p) > 80), parrafos[0] if parrafos else "")
    mejor = re.sub(r"\s+", " ", mejor).strip()
    if len(mejor) > ancho:
        mejor = mejor[:ancho].rsplit(" ", 1)[0] + " […]"
    return mejor


# ------------------------------------------------------------------- BUSCAR
def buscar(consulta: str = "", tipo: str = "", desde: int = 0, hasta: int = 0,
           materia: str = "", limite: int = 10) -> str:
    try:
        _cargar()
    except Exception as e:  # noqa: BLE001
        return f"La base de doctrina de la FGE no está disponible ({str(e)[:80]})."

    limite = max(1, min(int(limite or 10), 40))
    consulta = (consulta or "").strip()

    # 1) cita directa -> ficha inmediata
    ref = _detectar_cita(consulta)
    if ref:
        if ref in _POR_REF:
            d = _CAT[_POR_REF[ref]]
            texto = ""
            try:
                texto = _texto_de(ref)
            except Exception:  # noqa: BLE001
                pass
            out = [f"Encontrada la {_cita_corta(d)} (doctrina de la Fiscalía General del Estado).\n",
                   f"1. {_cita_corta(d)} — {d['titulo']}",
                   f"   Fecha: {_fecha_bonita(d)}  ·  Ref. {d['ref']}"]
            if d["materias"]:
                out.append(f"   Materias: {', '.join(d['materias'])}")
            if texto:
                out.append(f"   Comienzo: {_snippet(texto, [])}")
            if d["relacionadas"]:
                rel = [_cita_corta(_CAT[_POR_REF[r]]) for r in d["relacionadas"]
                       if r in _POR_REF][:6]
                if rel:
                    out.append(f"   Doctrina relacionada: {', '.join(rel)}")
            out.append(f"   PDF oficial: {_pdf(ref)}")
            out.append(f"\nPara el texto íntegro: leer_doctrina_fiscalia(\"{_cita_corta(d)}\").")
            return "\n".join(out)
        return (f"No existe {ref} en la colección oficial de doctrina de la FGE "
                "(1979-2026). Revisa número y año, o busca por tema.")

    # 2) filtros
    letra = _TIPO_LETRA.get(_quitar_tildes((tipo or "").strip().lower()), "")
    mat_norm = _quitar_tildes((materia or "").strip().lower())

    def _pasa(d):
        if letra and d["ref"].split("-")[1] != letra:
            return False
        if desde and d["anno"] < int(desde):
            return False
        if hasta and d["anno"] > int(hasta):
            return False
        if mat_norm and not any(mat_norm in _quitar_tildes(m.lower())
                                for m in d["materias"]):
            return False
        return True

    # 3) sin texto de consulta -> listado cronológico filtrado
    if not consulta:
        sel = [d for d in _CAT if _pasa(d)]
        if not sel:
            return "Nada en la doctrina de la FGE con esos filtros."
        sel = sel[:limite]
        out = [f"{len(sel)} piezas de doctrina de la FGE (más recientes primero):\n"]
        for i, d in enumerate(sel, 1):
            out.append(f"{i}. {_cita_corta(d)} — {d['titulo']}")
            out.append(f"   {_fecha_bonita(d)} · Ref. {d['ref']}"
                       + (f" · {', '.join(d['materias'])}" if d["materias"] else ""))
        out.append("\nPara una: leer_doctrina_fiscalia(\"Circular 1/2025\") (o su Ref.).")
        return "\n".join(out)

    # 4) búsqueda BM25 (texto completo) + boost por título/materias
    q = _tokens(consulta)
    q_extra = []
    for s in _expandir(consulta):
        q_extra.extend(_tokens(s))
    if not q and not q_extra:
        return "Concreta algún término de búsqueda."

    N = _IDX["N"]
    avgdl = _IDX["avgdl"] or 1.0
    dl = _IDX["dl"]
    k1, b = 1.5, 0.75
    scores = Counter()

    def _acumular(term, peso):
        post = _IDX["txt"].get(term)
        if post:
            idf = math.log(1 + (N - len(post) + 0.5) / (len(post) + 0.5))
            for i, tf in post:
                den = tf + k1 * (1 - b + b * dl[i] / avgdl)
                scores[i] += peso * idf * (tf * (k1 + 1)) / den
        post_t = _IDX["tit"].get(term)
        if post_t:
            idf = math.log(1 + (N - len(post_t) + 0.5) / (len(post_t) + 0.5))
            for i, tf in post_t:
                scores[i] += peso * 2.2 * idf * min(tf, 2)

    for t in set(q):
        _acumular(t, 1.0)
    for t in set(q_extra) - set(q):
        _acumular(t, 0.45)

    candidatos = [(i, s) for i, s in scores.items() if _pasa(_CAT[i])]
    if not candidatos:
        pista = ""
        if letra or desde or hasta or mat_norm:
            pista = " Prueba a quitar filtros (tipo/años/materia)."
        return (f"Sin doctrina de la FGE para «{consulta}».{pista} "
                "La colección cubre circulares, consultas e instrucciones 1979-2026.")

    # exigencia mínima de cobertura: al menos la mitad de los términos de la
    # consulta (evita resultados por un solo término suelto en consultas largas)
    if len(set(q)) >= 3:
        fuertes = []
        for i, s in candidatos:
            presentes = sum(1 for t in set(q)
                            if any(pi == i for pi, _ in _IDX["txt"].get(t, []))
                            or any(pi == i for pi, _ in _IDX["tit"].get(t, [])))
            if presentes >= max(2, len(set(q)) // 2):
                fuertes.append((i, s))
        if fuertes:
            candidatos = fuertes

    # empate técnico -> más reciente primero (los abogados quieren lo vigente)
    candidatos.sort(key=lambda x: (-x[1], x[0]))
    top = candidatos[:limite]

    out = [f"{len(top)} resultados en la doctrina de la FGE para «{consulta}» "
           f"(de {len(candidatos)} relevantes; colección 1979-2026):\n"]
    objetivo = set(q) | set(q_extra)
    for pos, (i, s) in enumerate(top, 1):
        d = _CAT[i]
        out.append(f"{pos}. {_cita_corta(d)} — {d['titulo']}")
        linea2 = f"   {_fecha_bonita(d)} · Ref. {d['ref']}"
        if d["materias"]:
            linea2 += f" · {', '.join(d['materias'])}"
        out.append(linea2)
        try:
            sn = _snippet(_texto_de(d["ref"]), objetivo)
            if sn:
                out.append(f"   Pasaje: {sn}")
        except Exception:  # noqa: BLE001
            pass
    out.append("\nPara el texto íntegro de una: leer_doctrina_fiscalia(\"Circular 1/2025\") "
               "(vale también 'Consulta 2/2026', 'Instrucción 1/2015' o la Ref. FIS-…).")
    return "\n".join(out)


# --------------------------------------------------------------------- LEER
def leer(referencia: str, parrafos: int = 0, terminos: str = "",
         max_chars: int = 0, desde_char: int = 0) -> str:
    try:
        _cargar()
    except Exception as e:  # noqa: BLE001
        return f"La base de doctrina de la FGE no está disponible ({str(e)[:80]})."

    referencia = (referencia or "").strip()
    if not referencia:
        return ("Indica qué pieza leer: 'Circular 1/2025', 'Consulta 2/2026', "
                "'Instrucción 1/2015' o su Ref. (FIS-C-2025-00001).")

    ref = _detectar_cita(referencia)
    if not (ref and ref in _POR_REF):
        # resolución aproximada por título/tema: mejor candidato del índice
        q = _tokens(referencia)
        mejor = None
        if q:
            scores = Counter()
            for t in set(q):
                for i, tf in _IDX["tit"].get(t, []):
                    scores[i] += 2.0
                for i, tf in _IDX["txt"].get(t, [])[:200]:
                    scores[i] += 0.1
            if scores:
                mejor = scores.most_common(1)[0][0]
        if mejor is None:
            return (f"No identifico «{referencia}» en la doctrina de la FGE. "
                    "Búscala antes con buscar_doctrina_fiscalia.")
        ref = _CAT[mejor]["ref"]

    d = _CAT[_POR_REF[ref]]
    try:
        texto = _texto_de(ref)
    except Exception as e:  # noqa: BLE001
        return f"Localizada la {_cita_corta(d)} pero no pude leer su texto ({str(e)[:60]})."

    cab = [f"【{_cita_corta(d)} — Fiscalía General del Estado】",
           d["titulo"],
           f"Fecha: {_fecha_bonita(d)} · Ref. {d['ref']}"]
    if d["materias"]:
        cab.append("Materias: " + ", ".join(d["materias"]))
    if d["relacionadas"]:
        rel = [_cita_corta(_CAT[_POR_REF[r]]) for r in d["relacionadas"]
               if r in _POR_REF][:8]
        if rel:
            cab.append("Doctrina relacionada: " + ", ".join(rel))
    cab.append("PDF oficial: " + _pdf(ref))

    # modo pasajes: los N párrafos más relevantes
    if parrafos and int(parrafos) > 0:
        n = min(int(parrafos), 25)
        objetivo = [t for t in set(_tokens(terminos or referencia)) if len(t) >= 3]
        parts = [p for p in texto.split("\n\n") if len(p) > 60]
        puntuados = []
        for pi, p in enumerate(parts):
            pn = _quitar_tildes(p.lower())
            sc = sum(pn.count(t) for t in objetivo)
            puntuados.append((sc, pi, p))
        puntuados.sort(key=lambda x: (-x[0], x[1]))
        eleg = sorted(puntuados[:n], key=lambda x: x[1])
        cuerpo = "\n\n[…]\n\n".join(re.sub(r"\s+", " ", p).strip()
                                    for _, _, p in eleg)
        return "\n".join(cab) + (
            f"\n\n— Los {len(eleg)} pasajes más relevantes"
            + (f" para «{terminos}»" if terminos else "")
            + f" (texto completo: {len(texto):,} caracteres; "
            "pídelo sin 'parrafos' para leerlo entero) —\n\n" + cuerpo)

    # texto íntegro (con troceo si excede)
    tope = int(max_chars) if max_chars else 60000
    tope = max(5000, min(tope, 120000))
    ini = max(0, int(desde_char or 0))
    total = len(texto)
    trozo = texto[ini:ini + tope]
    aviso = ""
    if ini:
        aviso += f"\n— (continuación desde el carácter {ini:,}) —\n"
    fin = ini + len(trozo)
    pie = f"\n\n[Texto completo: {total:,} caracteres. Mostrados {ini:,}–{fin:,}."
    if fin < total:
        pie += (f" Para seguir: leer_doctrina_fiscalia(\"{_cita_corta(d)}\", "
                f"desde_char={fin}).")
    pie += "]"
    return "\n".join(cab) + "\n" + aviso + "\n" + trozo + pie


# ------------------------------------------------------------------ RESUMEN
def resumen() -> str:
    """Línea corta para la tool estado()."""
    try:
        _cargar()
        tipos = Counter(d["ref"].split("-")[1] for d in _CAT)
        return (f"doctrina FGE: {len(_CAT)} docs "
                f"(C={tipos.get('C',0)} I={tipos.get('I',0)} Q={tipos.get('Q',0)})")
    except Exception as e:  # noqa: BLE001
        return f"doctrina FGE: ERROR {str(e)[:60]}"
