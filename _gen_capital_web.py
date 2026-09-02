# -*- coding: utf-8 -*-
"""Utilidades comunes de los generadores «capital vía su web propia»
(_gen_catalogo_cuenca.py, _gen_catalogo_guadalajara.py, _gen_catalogo_ceuta.py).
Offline: excluido del deploy por el patrón `_*`.

Patrón (brief 2-sep-2026): el ayuntamiento publica sus ordenanzas CONSOLIDADAS en su
web (PDF/HTML por norma) -> catálogo ordenanzas_data/<codigo>.json con meta.nombre +
meta.aliases (el motor lo registra solo, `_registrar_catalogos_auto`) -> texto
empaquetado por _fill_textos.py -> lectura local en <1 s.

Piezas:
  * get(): descarga educada (reintentos con espera, UA de navegador).
  * validar_pdf(): 200 + %PDF + capa de texto (o marca «escaneado» para OCR).
  * doc_a_texto(): .doc de Word 97 -> texto (antiword; respaldo Word COM).
  * alias_contenido(): alias por CONTENIDO del texto ya empaquetado. Motivo: la
    «Ordenanza Municipal de Medio Ambiente» de Cuenca regula ruido y animales, pero su
    título no lo dice; sin esto «ruido» caía en la tasa por medición de ruidos.
    Umbral alto (≥ MIN_MENCIONES menciones) para no robar consultas a la norma
    principal de otra materia.
  * fecha_de_nombre(): fecha/año legible a partir del nombre del fichero.
  * escribir_catalogo() / enriquecer_catalogo(): E/S del json conservando `texto`.
"""
import gzip
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "ordenanzas_data")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
      "Accept-Language": "es-ES,es;q=0.9"}


def norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def get(url: str, timeout: int = 40, intentos: int = 4) -> bytes:
    """GET con reintentos y espera creciente (las webs municipales van a rachas)."""
    ultimo = ""
    for i in range(intentos):
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout)
            b = r.read()
            if r.status == 200:
                return b
            ultimo = f"HTTP {r.status}"
        except urllib.error.HTTPError as e:
            if e.code in (404, 410):
                raise
            ultimo = f"HTTP {e.code}"
        except Exception as e:  # noqa: BLE001
            ultimo = f"{type(e).__name__}: {str(e)[:80]}"
        time.sleep(1.5 + 1.5 * i)
    raise RuntimeError(ultimo)


def url_abs(base: str, href: str) -> str:
    """href relativo/protocol-relative -> absoluto y con espacios/tildes escapados
    (urllib rechaza espacios en la ruta: «URL can't contain control characters»)."""
    href = href.strip()
    if href.startswith("//"):
        href = "https:" + href
    elif href.startswith("/"):
        href = base.rstrip("/") + href
    p = urllib.parse.urlsplit(href)
    path = urllib.parse.quote(urllib.parse.unquote(p.path), safe="/%()[],;:@!$&'*+=~-._")
    return urllib.parse.urlunsplit((p.scheme, p.netloc, path, p.query, ""))


def validar_pdf(url: str):
    """(estado, paginas, chars_por_pagina). estado: OK | ESCANEADO | NO_PDF | HTTP n | ERR x."""
    try:
        datos = get(url)
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}", 0, 0
    except Exception as e:  # noqa: BLE001
        return f"ERR {str(e)[:40]}", 0, 0
    if datos[:5] != b"%PDF-":
        return "NO_PDF", 0, 0
    try:
        import fitz
        doc = fitz.open(stream=datos, filetype="pdf")
        n = doc.page_count
        t = "\n".join(doc[i].get_text() for i in range(min(n, 8)))
        cpp = len(t) // max(1, min(n, 8))
        doc.close()
        return ("OK" if cpp >= 250 else "ESCANEADO"), n, cpp
    except Exception as e:  # noqa: BLE001
        return f"ERR pdf {str(e)[:30]}", 0, 0


def doc_a_texto(datos: bytes) -> str:
    """Word 97 (.doc) -> texto. 1) antiword (mingw64) con mapa UTF-8; 2) Word por COM."""
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".doc")
    os.close(fd)
    try:
        with open(tmp, "wb") as f:
            f.write(datos)
        for cmd in (["antiword", "-m", "UTF-8.txt", "-w", "0", tmp], ["antiword", "-w", "0", tmp]):
            try:
                r = subprocess.run(cmd, capture_output=True, timeout=60)
                if r.returncode == 0 and len(r.stdout) > 500:
                    enc = "utf-8" if "-m" in cmd else "latin-1"
                    return r.stdout.decode(enc, "replace")
            except Exception:  # noqa: BLE001
                continue
        try:  # respaldo: Word instalado (win32com)
            import win32com.client  # type: ignore
            w = win32com.client.Dispatch("Word.Application")
            w.Visible = False
            d = w.Documents.Open(os.path.abspath(tmp), ReadOnly=True)
            t = d.Content.Text
            d.Close(False)
            w.Quit()
            return t.replace("\r", "\n")
        except Exception:  # noqa: BLE001
            return ""
    finally:
        try:
            os.remove(tmp)   # scratch propio del script (no es un archivo de Carlos)
        except OSError:
            pass


# ---- alias por CONTENIDO -----------------------------------------------------
# (alias que se añaden, regex sobre el texto, mínimo de menciones)
MIN_MENCIONES = 15
MATERIAS_CONTENIDO = [
    (["ruido", "ruidos", "contaminacion acustica", "decibelios", "molestias por ruido"],
     r"\bruidos?\b|contaminaci[oó]n ac[uú]stica|decibel|\bvibraciones\b", 15),
    (["animales", "perros", "mascotas", "tenencia de animales", "ppp"],
     r"\banimal(?:es)?\b|\bperros?\b|\bmascotas?\b", 20),
    (["residuos", "basura", "basuras", "recogida de residuos", "contenedores"],
     r"\bresiduos?\b|\bbasuras?\b|\bcontenedor(?:es)?\b", 20),
    (["limpieza", "limpieza viaria", "pintadas", "grafitis"],
     r"\blimpieza\b|\bpintadas\b|\bgrafiti", 15),
    (["terraza", "terrazas", "veladores", "mesas y sillas"],
     r"\bterrazas?\b|\bveladores?\b|mesas y sillas", 15),
    (["venta ambulante", "mercadillo", "mercadillos"],
     r"venta ambulante|\bmercadillos?\b|no sedentaria", 12),
    (["vado", "vados", "entrada de vehiculos"],
     r"\bvados?\b", 12),
    (["estacionamiento", "aparcamiento", "zona azul", "ora", "estacionamiento regulado"],
     r"estacionamiento regulado|zona azul|\bO\.?R\.?A\.?\b", 10),
    (["zbe", "zona de bajas emisiones", "distintivo ambiental"],
     r"zona de bajas emisiones|\bZBE\b|distintivo ambiental", 8),
    (["patinete", "vmp", "vehiculos de movilidad personal"],
     r"movilidad personal|\bpatinetes?\b|\bVMP\b", 8),
    (["bicicleta", "bicicletas", "carril bici", "ciclistas"],
     r"\bbicicletas?\b|carril bici|\bciclistas?\b", 15),
    (["botellon", "alcohol", "bebidas alcoholicas", "consumo de alcohol en la via publica"],
     r"\bbotell[oó]n\b|bebidas alcoh[oó]licas|consumo de alcohol", 10),
    (["publicidad", "carteles", "vallas publicitarias", "rotulos"],
     r"\bpublicidad\b|\bcarteles\b|vallas publicitarias|\br[oó]tulos\b", 20),
    (["vertidos", "aguas residuales", "alcantarillado"],
     r"\bvertidos?\b|aguas residuales|\balcantarillado\b", 15),
    (["parques", "jardines", "zonas verdes", "arbolado"],
     r"\bparques\b|\bjardines\b|zonas verdes|\barbolado\b", 15),
    (["humos", "contaminacion atmosferica", "calidad del aire", "olores"],
     r"contaminaci[oó]n atmosf[eé]rica|calidad del aire|\bhumos\b|\bolores\b", 12),
    (["convivencia", "civismo", "conductas incivicas"],
     r"\bconvivencia\b|\bcivismo\b|inc[ií]vic", 12),
    (["hogueras", "fuegos artificiales", "pirotecnia", "petardos"],
     r"\bhogueras?\b|fuegos artificiales|\bpirotecnia\b|\bpetardos\b", 8),
    (["fiestas", "verbenas", "espectaculos publicos"],
     r"\bverbenas?\b|espect[aá]culos p[uú]blicos|actividades recreativas", 12),
    (["incendios", "proteccion contra incendios"],
     r"\bincendios?\b|extinci[oó]n de incendios", 15),
    (["ocupacion de via publica", "dominio publico", "via publica"],
     r"ocupaci[oó]n de (?:la )?v[ií]a p[uú]blica|dominio p[uú]blico local", 15),
]


def alias_contenido(texto: str, titulo: str = "") -> list:
    """Alias que el TEXTO justifica (materias con muchas menciones) y que el título
    no cubre ya. Devuelve lista (puede ser vacía)."""
    out = []
    tn = norm(titulo)
    for alias, rx, minimo in MATERIAS_CONTENIDO:
        if len(re.findall(rx, texto, re.I)) >= minimo:
            for a in alias:
                if a not in tn and a not in out:
                    out.append(a)
    return out


def leer_texto_empaquetado(codigo: str, norma: dict) -> str:
    d = os.path.join(DATA_DIR, codigo + "_textos")
    fich = norma.get("texto")
    if not fich:
        return ""
    fp = os.path.join(d, fich)
    if not os.path.exists(fp):
        return ""
    if fich.endswith(".gz"):
        with gzip.open(fp, "rt", encoding="utf-8") as f:
            return f.read()
    with open(fp, encoding="utf-8") as f:
        return f.read()


def enriquecer_catalogo(codigo: str) -> int:
    """Tras _fill_textos.py: añade alias por contenido a cada norma con texto.
    Devuelve el nº de normas enriquecidas. Idempotente."""
    fp = os.path.join(DATA_DIR, codigo + ".json")
    cat = json.load(open(fp, encoding="utf-8"))
    n = 0
    for norma in cat["normas"]:
        t = leer_texto_empaquetado(codigo, norma)
        if len(t) < 2000:
            continue
        nuevos = [a for a in alias_contenido(t, norma["titulo"]) if a not in norma.get("alias", [])]
        if nuevos:
            norma["alias"] = list(norma.get("alias", [])) + nuevos
            norma["alias_contenido"] = sorted(set(norma.get("alias_contenido", [])) | set(nuevos))
            n += 1
            print(f"  + {norma['id']}: {', '.join(nuevos)}   ({norma['titulo'][:60]})")
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(cat, f, ensure_ascii=False, indent=1)
    return n


# ---- fechas legibles a partir del nombre del fichero -------------------------
_MESES = {m: i for i, m in enumerate(
    ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
     "septiembre", "octubre", "noviembre", "diciembre"], 1)}


def fecha_de_nombre(nombre: str) -> str:
    """'BOP 10-02-2023' -> 'BOP 10/02/2023'; 'ORDENANZA TERRAZAS 2013' -> '2013';
    '-bop-boletines-2015-5-22-9' -> 'BOP 22/05/2015'; '' si no hay nada fiable."""
    s = urllib.parse.unquote(nombre)
    m = re.search(r"(\d{1,2})[-_/](\d{1,2})[-_/]((?:19|20)\d\d)", s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        if 1 <= d <= 31 and 1 <= mo <= 12:
            pref = "BOP " if re.search(r"(?i)\bbop\b", s) else ""
            return f"{pref}{d:02d}/{mo:02d}/{y}"
    m = re.search(r"(\d{1,2}) de (\w+) de ((?:19|20)\d\d)", s, re.I)
    if m and m.group(2).lower() in _MESES:
        pref = "BOP " if re.search(r"(?i)\bbop\b", s) else ""
        return f"{pref}{int(m.group(1)):02d}/{_MESES[m.group(2).lower()]:02d}/{m.group(3)}"
    m = re.search(r"bop[-_]boletines[-_]((?:19|20)\d\d)[-_](\d{1,2})[-_](\d{1,2})", s, re.I)
    if m:
        return f"BOP {int(m.group(3)):02d}/{int(m.group(2)):02d}/{m.group(1)}"
    m = re.search(r"\b((?:19|20)\d\d)\b", s)
    if m:
        return m.group(1)
    return ""


def escribir_catalogo(codigo: str, meta: dict, normas: list, conservar_texto: bool = True):
    """Escribe el json; si ya existía, conserva `texto`/`alias_contenido` por id."""
    fp = os.path.join(DATA_DIR, codigo + ".json")
    previas = {}
    if conservar_texto and os.path.exists(fp):
        try:
            for n in json.load(open(fp, encoding="utf-8")).get("normas", []):
                previas[n["id"]] = n
            meta_prev = json.load(open(fp, encoding="utf-8")).get("meta", {})
            for k in ("textos_dir", "textos_fecha"):
                if meta_prev.get(k) and not meta.get(k):
                    meta[k] = meta_prev[k]
        except Exception:  # noqa: BLE001
            previas = {}
    for n in normas:
        p = previas.get(n["id"])
        if p:
            if p.get("texto"):
                n["texto"] = p["texto"]
            if p.get("alias_contenido"):
                n["alias_contenido"] = p["alias_contenido"]
                n["alias"] = list(n.get("alias", [])) + [a for a in p["alias_contenido"]
                                                          if a not in n.get("alias", [])]
    with open(fp, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "normas": normas}, f, ensure_ascii=False, indent=1)
    return fp


def uniq(seq):
    vistos, out = set(), []
    for a in seq:
        if a and a not in vistos:
            vistos.add(a)
            out.append(a)
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) > 2 and sys.argv[1] == "--enriquecer":
        print(f"{enriquecer_catalogo(sys.argv[2])} normas enriquecidas")
