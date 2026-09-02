# -*- coding: utf-8 -*-
"""Genera ordenanzas_data/cuenca.json — catálogo de las ordenanzas CONSOLIDADAS del
Ayuntamiento de CUENCA capital publicadas en su web (patrón «capital vía su web
propia», como leon_capital). Offline/_gen (no se despliega).

Fuente: https://ayuntamiento.cuenca.es/ordenanzas-municipales (23 ordenanzas generales)
        https://ayuntamiento.cuenca.es/ordenanzas-fiscales   (50 ordenanzas fiscales)
Estructura de ambas páginas (DotNetNuke):
    <h3 class="formatearOrdenanza">TÍTULO</h3>
    <div class="listado item"> ... <a target='_blank' href='/Portals/Ayuntamiento/documents/<id>_<fichero>.pdf'>
Una ordenanza puede traer VARIOS documentos (texto refundido + cuadro de multas +
modificaciones publicadas en el BOP + anexos): el principal es el texto de la norma
(refundido/íntegro; si no, el de subida más reciente) y el resto queda en `anexos`
(informativo) o `urls` (versiones alternativas, respaldo de descarga).

Trampas:
  * Las URL llevan ESPACIOS y tildes -> se escapan (urllib rechaza espacios).
  * La ordenanza de bebidas alcohólicas es un .DOC de Word 97: el texto se extrae aquí
    (antiword) y se deja ya empaquetado; _fill_textos.py --solo-faltan lo respeta.
  * El BOP de Cuenca NO sirve de respaldo (bop_cuenca_config activo=false).

Uso:  python -X utf8 _gen_catalogo_cuenca.py            (genera el catálogo, valida URLs)
      python -X utf8 _fill_textos.py cuenca --workers 2 --gz --solo-faltan
      python -X utf8 _gen_catalogo_cuenca.py --enriquecer  (alias por contenido)
"""
import concurrent.futures as cf
import gzip
import html as H
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _gen_capital_web as W  # noqa: E402
from _gen_comun import alias_para, norm  # noqa: E402

CODIGO = "cuenca"
BASE = "https://ayuntamiento.cuenca.es"
PAGINAS = [("/ordenanzas-municipales", "general"), ("/ordenanzas-fiscales", "fiscal")]
TEXTOS = os.path.join(W.DATA_DIR, CODIGO + "_textos")

# documentos AUXILIARES (no son el texto de la norma): anexos, cuadros, coeficientes,
# modificaciones sueltas publicadas en el BOP, plantillas...
AUX = re.compile(r"(?i)anexo|cuadro|tablas_|criterios_|coef|decreto|dina[13]|declaraci|"
                 r"modificaci|\bbop\b|bop[-_]boletines|17agosto2016|E2AE6EAC")
# el texto principal, si hay varios candidatos
PRINCIPAL = re.compile(r"(?i)refundid|consolidad|integro|íntegro")

# alias curados por título normalizado (regex) -> alias añadidos al tesauro común
EXTRAS = [
    (r"antibotellon", ["botellon", "alcohol", "beber en la calle", "consumo de alcohol en la via publica"]),
    (r"ficheros de datos", ["proteccion de datos", "ficheros", "lopd", "datos personales"]),
    (r"terrazas de bar", ["ocupacion de via publica", "terraza de bar", "veladores"]),
    (r"paisaje urbano", ["paisaje urbano", "fachadas", "estetica urbana", "toldos", "rotulos"]),
    (r"comunicacion previa", ["comunicacion previa", "declaracion responsable", "obras menores",
                               "licencia de obras", "acto comunicado"]),
    (r"proyectos por via electronica", ["proyectos", "via electronica", "administracion electronica",
                                        "presentacion de proyectos"]),
    (r"planes parciales", ["planes parciales", "peri", "planeamiento", "reforma interior",
                           "contenido documental"]),
    (r"caracter urbanistico", ["normas urbanisticas", "pgou", "edificacion", "ordenanzas urbanisticas"]),
    (r"cartel informativo", ["cartel de obra", "cartel informativo", "cartel de licencia"]),
    (r"trafico", ["seguridad vial", "multas de trafico", "circulacion", "vados", "carga y descarga",
                  "estacionamiento", "aparcamiento", "casco antiguo", "movilidad"]),
    (r"urbanizacion", ["urbanizacion", "obras de urbanizacion", "proyectos de urbanizacion", "aceras"]),
    (r"alumbrado", ["alumbrado", "alumbrado publico", "iluminacion", "farolas"]),
    (r"taxi", ["taxi", "taxis", "autotaxi", "auto-taxi", "licencia de taxi", "vtc"]),
    (r"movilidad personal", ["vmp", "patinete", "patinetes", "movilidad personal", "patinete electrico"]),
    (r"incendios en el casco", ["casco historico", "casco antiguo", "incendios"]),
    (r"medio ambiente", ["medio ambiente", "proteccion ambiental", "contaminacion"]),
    (r"convivencia", ["convivencia", "civismo", "espacios publicos", "conductas incivicas"]),
    (r"gastos suntuarios", ["gastos suntuarios", "cotos de caza", "cotos"]),
    (r"retirada de vehiculos|grua", ["grua", "retirada de vehiculos", "deposito de vehiculos", "tasa de grua"]),
    (r"autotaxis", ["taxi", "taxis", "licencia de taxi", "autotaxi"]),
    (r"calicatas", ["calicatas", "zanjas", "obras en la via publica", "canalizaciones"]),
    (r"suelo vuelo y subsuelo", ["suelo vuelo y subsuelo", "empresas suministradoras", "1,5%", "1 5"]),
    (r"mercancias materiales de construccion", ["andamios", "vallas de obra", "contenedores de obra",
                                                 "escombros", "ocupacion de via publica"]),
    (r"puestos barracas", ["venta ambulante", "mercadillo", "puestos", "barracas", "ferias",
                           "atracciones", "food truck", "rodaje"]),
    (r"grandes transportes", ["caravanas", "transportes especiales", "espectaculos", "cortes de calle"]),
    (r"documentos administrativos", ["expedicion de documentos", "certificados", "compulsa",
                                     "tasa de documentos"]),
    (r"servicios urbanisticos", ["licencia urbanistica", "tasa de licencia de obras", "licencia de obras"]),
    (r"derechos de examen", ["derechos de examen", "oposiciones", "procesos selectivos"]),
    (r"cajeros", ["cajeros automaticos", "cajeros", "ventanas de despacho"]),
    (r"energia electrica gas agua", ["empresas suministradoras", "energia electrica", "gas",
                                     "hidrocarburos", "1,5%"]),
    (r"ayuda a domicilio", ["ayuda a domicilio", "sad", "dependencia", "servicios sociales"]),
    (r"matrimonios", ["bodas", "matrimonio civil", "boda civil"]),
    (r"kanguras", ["canguros", "conciliacion", "ludoteca", "kanguras"]),
    (r"instalaciones deportivas", ["deportes", "polideportivo", "piscina", "escuelas deportivas"]),
    (r"teatro", ["teatro", "auditorio", "teatro auditorio"]),
    (r"juventud", ["juventud", "actividades juveniles"]),
    (r"cuenca subterranea", ["visitas guiadas", "turismo", "cuenca subterranea"]),
    (r"escuelas infantiles", ["escuelas infantiles", "guarderia", "guarderias"]),
    (r"matadero", ["matadero", "acarreo de carnes"]),
    (r"libros", ["libros", "venta de libros"]),
    (r"consumidor", ["consumo", "consumidor", "omic"]),
    (r"escuela municipal de musica", ["escuela de musica", "conservatorio", "artes escenicas"]),
    (r"alcohol o para deteccion de drogas", ["alcoholemia", "drogas", "pruebas de alcoholemia", "etilometro"]),
    (r"iglesia de san miguel", ["san miguel", "iglesia de san miguel"]),
    (r"replanteo", ["obras municipales", "replanteo", "direccion de obra"]),
    (r"mercado de minoristas", ["mercado minorista", "abastos", "mercado de abastos", "puesto de mercado"]),
    (r"mercado de mayoristas", ["mercado mayorista", "mercasa", "lonja"]),
    (r"medicion de ruidos", ["medicion de ruidos", "sonometro", "tasa de ruido"]),
    (r"recogida transporte y tratamiento de residuos", ["tasa de basura", "tasa de residuos",
                                                         "recogida de basuras", "residuos solidos urbanos"]),
    (r"gestion recaudacion e inspeccion", ["ordenanza fiscal general", "recaudacion", "aplazamiento",
                                           "fraccionamiento", "apremio", "inspeccion tributaria"]),
    (r"bienes inmuebles", ["ibi", "bonificacion ibi", "familia numerosa"]),
    (r"vehiculos traccion mecanica", ["ivtm", "impuesto de circulacion", "impuesto de vehiculos"]),
    (r"instalaciones publicitarias", ["publicidad", "rotulos", "carteles", "banderolas", "publicidad exterior"]),
    (r"transparencia", ["transparencia", "acceso a la informacion", "buen gobierno", "reutilizacion"]),
    (r"prevencion de incendios", ["incendios", "proteccion contra incendios", "pci", "bomberos"]),
    (r"rehabilitacion y ruina", ["ruina", "rehabilitacion", "conservacion de edificios", "ite", "orden de ejecucion"]),
    (r"depuradora", ["depuradora", "depuracion", "edar"]),
    (r"suministro del agua", ["agua", "tarifa del agua", "abastecimiento", "suministro de agua"]),
]


def cat_de(titulo: str, pagina: str) -> str:
    t = norm(titulo)
    if pagina == "fiscal":
        if "impuesto" in t:
            return "Impuestos"
        if "precio publico" in t or "precio p" in t:
            return "Precios públicos"
        if "tasa" in t:
            return "Tasas"
        return "Fiscales"
    if re.search(r"trafico|taxi|movilidad", t):
        return "Tráfico y movilidad"
    if re.search(r"medio ambiente|incendios", t):
        return "Medio ambiente y seguridad"
    if re.search(r"urbanistic|urbanizacion|edificaciones|alumbrado|cartel|planes parciales|"
                 r"comunicacion previa|proyectos", t):
        return "Urbanismo"
    if re.search(r"terrazas|publicitari|paisaje", t):
        return "Vía pública"
    if re.search(r"botellon|alcoholicas|convivencia", t):
        return "Convivencia"
    if re.search(r"transparencia|ficheros", t):
        return "Administración"
    if re.search(r"tributos|recaudacion", t):
        return "Fiscales"
    return "Ordenanzas"


def parsear(htm: str, pagina: str):
    """[(titulo, [(url_abs, nombre_fichero), ...])] en el orden de la página."""
    out = []
    partes = re.split(r'<h3 class="formatearOrdenanza">', htm)[1:]
    for p in partes:
        m = re.match(r"([^<]*)</h3>", p)
        if not m:
            continue
        titulo = re.sub(r"\s+", " ", H.unescape(m.group(1))).strip().rstrip(".").strip()
        # corrige erratas de la propia web ("ORDENZA FISCAL")
        titulo = re.sub(r"(?i)^ORDENZA\b", "ORDENANZA", titulo)
        titulo = re.sub(r"(?i)\bANRTIGUA\b", "ANTIGUA", titulo)
        docs = []
        for href in re.findall(r"<a[^>]+href=['\"]([^'\"]+)['\"]", p):
            if "/Portals/" not in href:
                continue                       # enlaces externos al BOP (frameset viejo): fuera
            if not re.search(r"(?i)\.(pdf|docx?)$", href):
                continue
            url = W.url_abs(BASE, href)
            nombre = os.path.basename(href)
            if url not in [u for u, _ in docs]:
                docs.append((url, nombre))
        if docs:
            out.append((titulo, docs))
    return out


def elegir_principal(docs):
    """(principal, alternativas, anexos) según AUX/PRINCIPAL y el id de subida (mayor = más nuevo)."""
    def docid(nombre):
        m = re.match(r"(\d+)_", nombre)
        return int(m.group(1)) if m else 0
    cuerpo = [d for d in docs if not AUX.search(d[1])] or docs
    pri = [d for d in cuerpo if PRINCIPAL.search(d[1])]
    if pri:
        principal = pri[0]
    else:
        principal = max(cuerpo, key=lambda d: docid(d[1]))
    alternativas = [d for d in cuerpo if d is not principal]
    anexos = [d for d in docs if d not in cuerpo]
    return principal, alternativas, anexos


def titulo_bonito(t: str) -> str:
    """La web mezcla MAYÚSCULAS y minúsculas: normaliza a «Ordenanza fiscal reguladora de…»."""
    letras = [c for c in t if c.isalpha()]
    if letras and sum(1 for c in letras if c.isupper()) / len(letras) >= 0.9:   # "PRECIO PúBLICO" cuenta
        t = t.lower()
        t = re.sub(r"\s+", " ", t)
        t = t[:1].upper() + t[1:]
        for sig in ("ibi", "iae", "ivtm", "icio", "ora", "iivtnu"):
            t = re.sub(rf"\b{sig}\b", sig.upper(), t)
        t = re.sub(r"\bcuenca\b", "Cuenca", t)
        t = re.sub(r"\bexcmo\.? ayuntamiento\b", "Excmo. Ayuntamiento", t)
    return t


def main(enriquecer=False):
    if enriquecer:
        n = W.enriquecer_catalogo(CODIGO)
        print(f"{n} normas enriquecidas con alias por contenido")
        return
    os.makedirs(TEXTOS, exist_ok=True)
    normas, fallos = [], []
    idx = 0
    for ruta, pagina in PAGINAS:
        htm = W.get(BASE + ruta).decode("utf-8", "replace")
        entradas = parsear(htm, pagina)
        print(f"{ruta}: {len(entradas)} ordenanzas")
        for titulo, docs in entradas:
            idx += 1
            principal, alternativas, anexos = elegir_principal(docs)
            url, nombre = principal
            formato = "doc" if re.search(r"(?i)\.docx?$", nombre) else "pdf"
            alias = alias_para(titulo)
            tn = norm(titulo)
            for rx, extra in EXTRAS:
                if re.search(rx, tn):
                    alias.extend(extra)
            n = {"id": f"{CODIGO}-{idx:03}", "titulo": titulo_bonito(titulo),
                 "cat": cat_de(titulo, pagina), "ref": "", "pub": W.fecha_de_nombre(nombre),
                 "mod": "", "alias": W.uniq(alias), "url": url, "formato": formato}
            if alternativas:
                n["urls"] = [u for u, _ in alternativas]
            if anexos:
                n["anexos"] = [u for u, _ in anexos]
            normas.append(n)

    # validación en vivo (2 hilos: educados con la web municipal)
    def validar(n):
        if n["formato"] == "doc":
            try:
                datos = W.get(n["url"])
                if datos[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
                    return n["id"], "NO_DOC", 0, 0
                t = W.doc_a_texto(datos)
                if len(t) < 500:
                    return n["id"], "DOC_SIN_TEXTO", 0, 0
                fp = os.path.join(TEXTOS, n["id"] + ".txt.gz")
                with gzip.open(fp, "wt", encoding="utf-8") as f:
                    f.write(t)
                n["texto"] = n["id"] + ".txt.gz"
                return n["id"], "OK", 0, len(t)
            except Exception as e:  # noqa: BLE001
                return n["id"], f"ERR {str(e)[:30]}", 0, 0
        est, pags, cpp = W.validar_pdf(n["url"])
        return n["id"], est, pags, cpp

    with cf.ThreadPoolExecutor(max_workers=2) as ex:
        res = {nid: (est, pags, cpp) for nid, est, pags, cpp in ex.map(validar, normas)}
    buenas = []
    for n in normas:
        est, pags, cpp = res[n["id"]]
        if est in ("OK", "ESCANEADO"):
            print(f"✅ [{est:9}] {pags:3d}p {cpp:5d}c/p  {n['id']} {n['titulo'][:66]}")
            buenas.append(n)
        else:
            print(f"❌ [{est:12}]  {n['id']} {n['titulo'][:60]}  {n['url']}")
            fallos.append(n)
    meta = {"municipio": CODIGO, "nombre": "Cuenca",
            "aliases": ["cuenca", "ayuntamiento de cuenca", "cuenca capital", "ciudad de cuenca",
                        "excmo ayuntamiento de cuenca"],
            "fuente": "Ayuntamiento de Cuenca (ordenanzas municipales y fiscales consolidadas, PDF oficial por norma)",
            "url": BASE + "/ordenanzas-municipales", "url_fiscales": BASE + "/ordenanzas-fiscales",
            "actualizado": "2026-09",
            "nota": "Una ordenanza puede traer varios documentos: `url` es el texto de la norma, "
                    "`urls` versiones alternativas y `anexos` cuadros/coeficientes/modificaciones BOP."}
    fp = W.escribir_catalogo(CODIGO, meta, buenas)
    print(f"\n{len(buenas)} normas escritas en {fp}" + (f" · {len(fallos)} fallos" if fallos else ""))


if __name__ == "__main__":
    main(enriquecer="--enriquecer" in sys.argv)
