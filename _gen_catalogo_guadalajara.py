# -*- coding: utf-8 -*-
"""Genera ordenanzas_data/guadalajara.json — catálogo de las ordenanzas y reglamentos
CONSOLIDADOS del Ayuntamiento de GUADALAJARA capital publicados en su web (patrón
«capital vía su web propia», como leon_capital). Offline/_gen (no se despliega).

Fuente (3 páginas del mismo portal):
  /es/ayuntamiento/normativa/ordenanzas-generales/    (28 ordenanzas)
  /es/ayuntamiento/normativa/ordenanzas-fiscales/     (48 vigentes, por secciones <h2>)
  /es/ayuntamiento/normativa/reglamentos-y-estatutos/ (40 reglamentos)
Estructura: <li><a class="documento" href="//www.guadalajara.es/recursos/doc/portal/...pdf">
            2025 - Ordenanza de X (BOP 2025-05-27) (entrada en vigor 2025-06-18)</a>
            [-- <a class="documento">Cuadro sancionador</a> ...]</li>
El primer enlace del <li> es la norma; los siguientes son anexos (cuadro sancionador,
coeficientes, rectificaciones) o una versión alternativa del texto («Ordenanza»).

Trampas:
  * Los href son protocol-relative («//www...») -> se les pone https:.
  * El título trae año, BOP y vigencia entre paréntesis -> van a `pub`, no al título.
  * Los impuestos van numerados («5. Impuesto sobre bienes inmuebles») -> ref «O.F. 5» y
    título «Ordenanza fiscal reguladora del Impuesto…» (así el ranking le da el bonus de
    «ordenanza» y la cabecera se lee como una norma).
  * El BOP de Guadalajara NO sirve de respaldo (bop_guadalajara_config activo=false, lento).

Uso:  python -X utf8 _gen_catalogo_guadalajara.py
      python -X utf8 _fill_textos.py guadalajara --workers 2 --gz --solo-faltan
      python -X utf8 _gen_catalogo_guadalajara.py --enriquecer
"""
import concurrent.futures as cf
import html as H
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import _gen_capital_web as W  # noqa: E402
from _gen_comun import alias_para, norm  # noqa: E402

CODIGO = "guadalajara"
BASE = "https://www.guadalajara.es"
PAGINAS = [("/es/ayuntamiento/normativa/ordenanzas-generales/", "Ordenanzas"),
           ("/es/ayuntamiento/normativa/ordenanzas-fiscales/", "Fiscales"),
           ("/es/ayuntamiento/normativa/reglamentos-y-estatutos/", "Reglamentos")]
SECCION_CAT = [(r"impuestos", "Impuestos"), (r"tasas", "Tasas"), (r"precios p", "Precios públicos"),
               (r"contribuciones", "Contribuciones especiales"), (r"fiscal general", "Fiscal general"),
               (r"ejercicios anteriores", None)]

EXTRAS = [
    (r"estacionamiento limitado", ["ora", "zona azul", "estacionamiento regulado", "parquimetro",
                                   "control horario"]),
    (r"prestaci[oó]n patrimonial|prestaciones patrimoniales", ["tarifas", "prestacion patrimonial"]),
    (r"transporte|autobus", ["autobus", "autobuses", "transporte urbano", "billete", "bonobus"]),
    (r"espacios libres", ["espacios libres", "obras en via publica", "zanjas", "calas", "canalizaciones"]),
    (r"festejos taurinos", ["toros", "encierros", "festejos taurinos", "taurino", "vaquillas"]),
    (r"aparcamientos publicos", ["aparcamiento", "parking", "aparcamientos publicos", "aparcamiento subterraneo"]),
    (r"patrocinios", ["patrocinio", "patrocinios", "mecenazgo", "esponsorizacion"]),
    (r"abastos", ["abastos", "mercado de abastos", "puesto de mercado"]),
    (r"termica", ["termica", "calefaccion", "aire acondicionado", "vibraciones", "aislamiento acustico"]),
    (r"normas cartograficas", ["cartografia", "planos", "topografia"]),
    (r"antenas", ["antenas", "telefonia movil", "radiocomunicacion", "estaciones base"]),
    (r"venta de prensa", ["quiosco de prensa", "venta de prensa", "prensa", "quiosco", "quioscos"]),
    (r"proteccion de bienes", ["bienes municipales", "patrimonio municipal", "mobiliario urbano",
                               "vandalismo", "danos a bienes"]),
    (r"taxi", ["taxi", "taxis", "autotaxi", "auto-taxi", "licencia de taxi", "vtc"]),
    (r"aguas residuales", ["vertidos", "aguas residuales", "alcantarillado", "saneamiento", "depuracion"]),
    (r"apertura de establecimientos", ["licencia de apertura", "licencia de actividad", "apertura",
                                       "declaracion responsable"]),
    (r"dependencias e instalaciones", ["alquiler de salas", "instalaciones municipales", "cesion de espacios"]),
    (r"documentos administrativos", ["expedicion de documentos", "certificados", "compulsa"]),
    (r"derechos de examen", ["derechos de examen", "oposiciones", "procesos selectivos"]),
    (r"cotilla|teatro", ["teatro", "teatro moderno", "teatro buero vallejo", "auditorio", "la cotilla"]),
    (r"monumentos", ["monumentos", "turismo", "entradas", "visitas"]),
    (r"control animal", ["animales", "perros", "recogida de animales", "perrera", "tasa de animales"]),
    (r"policia local", ["policia local", "policia municipal", "servicios especiales"]),
    (r"estacionamiento de autobuses", ["estacion de autobuses", "autobuses"]),
    (r"mercancias", ["andamios", "vallas de obra", "contenedores de obra", "escombros",
                     "ocupacion de via publica", "materiales de construccion"]),
    (r"utilizacion privativa|suelo y vuelo|subsuelo", ["suelo vuelo y subsuelo", "empresas suministradoras",
                                                       "1,5%", "1 5"]),
    (r"puestos barracas", ["venta ambulante", "mercadillo", "puestos", "barracas", "ferias", "atracciones",
                           "food truck"]),
    (r"vallas puntales", ["andamios", "vallas", "puntales", "asnillas", "ocupacion de via publica"]),
    (r"calicatas", ["calicatas", "zanjas", "obras en la via publica", "canalizaciones"]),
    (r"recogida de vehiculos", ["grua", "retirada de vehiculos", "deposito de vehiculos", "tasa de grua",
                                "inmovilizacion"]),
    (r"entrada de vehiculos|con ent", ["vado", "vados", "entrada de vehiculos", "paso de vehiculos"]),
    (r"ayuda a domicilio", ["ayuda a domicilio", "sad", "dependencia", "servicios sociales"]),
    (r"escuelas culturales", ["escuelas culturales", "talleres", "cursos"]),
    (r"escuelas deportivas|natacion", ["deportes", "polideportivo", "piscina", "escuelas deportivas",
                                       "instalaciones deportivas"]),
    (r"prestamo de bicicletas", ["bicicleta publica", "prestamo de bicicletas", "bici"]),
    (r"escuelas infantiles|atencion a la infancia", ["escuelas infantiles", "guarderia", "guarderias",
                                                     "primer ciclo de infantil"]),
    (r"articulos de recuerdo|libros", ["souvenirs", "libros", "venta de libros", "oficina de turismo"]),
    (r"contribucion especial", ["contribuciones especiales", "bomberos", "extincion de incendios"]),
    (r"honores y distinciones", ["honores", "distinciones", "medallas", "hijo predilecto", "hijo adoptivo"]),
    (r"teletrabajo", ["teletrabajo", "empleados publicos", "trabajo a distancia"]),
    (r"consejo", ["consejo sectorial", "organos de participacion"]),
    (r"biblioteca", ["biblioteca", "bibliotecas", "prestamo de libros"]),
    (r"carnet joven|carne joven", ["carnet joven", "carne joven", "juventud"]),
    (r"plaza de toros", ["toros", "plaza de toros", "festejos taurinos"]),
    (r"espacio tyce|centro joven", ["juventud", "centro joven", "tyce"]),
    (r"reglamento organico del gobierno", ["reglamento organico", "organizacion municipal",
                                           "junta de gobierno", "concejales", "alcalde"]),
    (r"mayores", ["mayores", "personas mayores", "tercera edad"]),
    (r"registro contable de facturas", ["facturas", "registro contable", "factura electronica", "proveedores"]),
    (r"consejo escolar", ["consejo escolar", "educacion", "colegios"]),
    (r"centros sociales", ["centros sociales", "centro social", "asociaciones"]),
    (r"reglamento organico del pleno", ["pleno", "plenos", "mociones", "reglamento organico", "concejales",
                                        "ruegos y preguntas"]),
    (r"igualdad", ["igualdad", "mujeres", "violencia de genero"]),
    (r"gestion tributaria", ["oficina tributaria", "gestion tributaria", "recaudacion", "tributos"]),
    (r"economico.administrativ", ["reclamacion economico administrativa", "tribunal economico administrativo",
                                  "team", "recurso tributario"]),
    (r"uniones civiles|de hecho", ["parejas de hecho", "registro de parejas de hecho", "uniones de hecho"]),
    (r"segunda actividad", ["segunda actividad", "policia local"]),
    (r"ludotecas", ["ludoteca", "ludotecas", "conciliacion"]),
    (r"infancia y la adolescencia", ["infancia", "adolescencia", "menores"]),
    (r"accesibilidad", ["accesibilidad", "barreras arquitectonicas", "discapacidad", "movilidad reducida"]),
    (r"cooperacion", ["cooperacion al desarrollo", "cooperacion internacional", "ong"]),
    (r"sugerencias y reclamaciones", ["quejas", "sugerencias", "reclamaciones", "defensor del ciudadano"]),
    (r"regimen juridico de la policia", ["policia local", "policia municipal", "agentes"]),
    (r"proteccion civil", ["proteccion civil", "voluntariado", "agrupacion de voluntarios", "emergencias"]),
    (r"cementerios", ["cementerio", "cementerios", "servicios funerarios", "tanatorio", "enterramiento"]),
    (r"funerarios", ["funerarias", "servicios funerarios", "tanatorio", "velatorio"]),
    (r"inspeccion tecnica", ["ite", "inspeccion tecnica de edificios", "iee"]),
    (r"convivencia ciudadana", ["convivencia", "civismo", "botellon", "conductas incivicas", "pintadas",
                                "espacio publico"]),
    (r"limpieza viaria", ["limpieza", "limpieza viaria", "higiene urbana", "pintadas", "excrementos",
                          "residuos", "basura"]),
    (r"tenencia y proteccion de animales", ["animales", "perros", "ppp", "perros peligrosos", "mascotas",
                                            "colonias felinas", "excrementos"]),
    (r"terrazas", ["terraza", "terrazas", "veladores", "mesas y sillas", "hosteleria", "toldos"]),
    (r"movilidad", ["movilidad", "trafico", "circulacion", "estacionamiento", "aparcamiento", "bicicleta",
                    "patinete", "vmp", "peatones", "multas de trafico", "carga y descarga", "vados",
                    "seguridad vial"]),
    (r"bajas emisiones", ["zbe", "zona de bajas emisiones", "distintivo ambiental", "restricciones de trafico",
                          "etiqueta ambiental"]),
    (r"administracion electronica", ["administracion electronica", "sede electronica", "registro electronico",
                                     "notificaciones electronicas"]),
    (r"subvenciones", ["subvenciones", "ayudas", "bases reguladoras"]),
    (r"actividades publicitarias", ["publicidad", "publicidad exterior", "carteles", "vallas publicitarias",
                                    "rotulos", "publicidad en via publica"]),
    (r"venta ambulante", ["venta ambulante", "mercadillo", "mercadillos", "puestos ambulantes", "food truck"]),
    (r"parques y jardines", ["parques", "jardines", "zonas verdes", "arbolado", "areas de juego"]),
    (r"gastos suntuarios", ["gastos suntuarios", "cotos de caza"]),
    (r"recogida de basuras", ["tasa de basura", "tasa de residuos", "basura", "residuos"]),
    (r"licencias urbanisticas", ["licencia urbanistica", "licencia de obras", "tasa de licencia de obras"]),
    (r"licencia y autorizaciones administrativas de auto", ["taxi", "licencia de taxi", "autotaxi"]),
    (r"fiscal general", ["ordenanza fiscal general", "recaudacion", "aplazamiento", "fraccionamiento",
                         "inspeccion tributaria", "apremio"]),
    (r"agua", ["agua", "tarifa del agua", "abastecimiento", "suministro de agua"]),
]


def limpiar_titulo(raw: str):
    """'2025 - Ordenanza de X (texto íntegro BOP) (entrada en vigor 2025-06-18)'
    -> (titulo, pub, ref)."""
    t = re.sub(r"\s+", " ", H.unescape(raw)).strip()
    raw = t                       # ya sin entidades HTML («&iacute;ntegro» -> «íntegro»)
    pub = []
    ref = ""
    m = re.match(r"^((?:19|20)\d\d)\s*-\s*", t)
    if m:
        pub.append(m.group(1))
        t = t[m.end():]
    m = re.match(r"^(\d{1,2})\.\s+", t)
    if m:
        ref = f"O.F. {int(m.group(1))}"
        t = t[m.end():]
    t = re.sub(r"^[>\-–]\s*", "", t)
    for rx, etiqueta in ((r"\(?\s*BOP:?\s*((?:19|20)\d\d)-(\d\d)-(\d\d)\s*\)?", "BOP {d}/{m}/{y}"),
                         (r"\(\s*vigente desde:?\s*((?:19|20)\d\d)-(\d\d)-(\d\d)\s*\)", "vigente desde {d}/{m}/{y}"),
                         (r"\(\s*entrada en vigor:?\s*((?:19|20)\d\d)-(\d\d)-(\d\d)\s*\)", "en vigor desde {d}/{m}/{y}"),
                         (r"\(\s*vigencia desde el?\s*([^)]+)\)", "vigencia desde {txt}"),
                         (r"\(\s*entrada en vigor:?\s*([^)]+)\)", "en vigor desde {txt}")):
        m = re.search(rx, t, re.I)
        if m:
            if "{txt}" in etiqueta:
                pub.append(etiqueta.format(txt=m.group(1).strip()))
            else:
                pub.append(etiqueta.format(y=m.group(1), m=m.group(2), d=m.group(3)))
            t = t[:m.start()] + t[m.end():]
    t = re.sub(r"\((?:texto [ií]ntegro[^)]*|entrada en vigor[^)]*)\)", "", t, flags=re.I)
    t = re.sub(r"\[texto consolidado\]", "", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip(" .-")
    if re.match(r"(?i)^Modificaci[oó]n del (Reglamento|Ordenanza)", t) and "íntegro" in raw.lower():
        t = re.sub(r"(?i)^Modificaci[oó]n del\s+", "", t)
        t = re.sub(r"(?i)\.\s*Texto [ií]ntegro\s*$", "", t)
        t += f" (texto íntegro tras la modificación de {pub[0]})" if pub else " (texto íntegro)"
    if re.match(r"(?i)^Impuesto\b", t):
        t = "Ordenanza fiscal reguladora del " + t
    if re.match(r"(?i)^Precio p[uú]blico\b", t):
        t = "Acuerdo regulador del " + t[:1].lower() + t[1:]
    return t, " · ".join(pub), ref


def parsear(htm: str, cat_pagina: str):
    """[(titulo, pub, ref, cat, principal, alternativas, anexos)] en orden de página."""
    out = []
    cat = cat_pagina
    for m in re.finditer(r"<h2[^>]*>(.*?)</h2>|<li>(.*?)</li>", htm, re.S):
        if m.group(1) is not None:
            if cat_pagina == "Fiscales":
                sec = norm(re.sub("<[^>]+>", "", m.group(1)))
                for rx, c in SECCION_CAT:
                    if re.search(rx, sec):
                        cat = c
                        break
            continue
        li = m.group(2)
        links = re.findall(r'<a class="documento"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', li, re.S)
        if not links or cat is None:
            continue
        href, raw = links[0]
        titulo, pub, ref = limpiar_titulo(re.sub("<[^>]+>", "", raw))
        if not ref:
            mm = re.search(r"/(\d{2})-(?:ordenanza|impuesto|vigente)", href)
            if mm and cat_pagina == "Fiscales":
                ref = f"O.F. {int(mm.group(1))}"
        principal = W.url_abs(BASE, href)
        alternativas, anexos = [], []
        for h2, t2 in links[1:]:
            t2 = re.sub(r"\s+", " ", H.unescape(re.sub("<[^>]+>", "", t2))).strip(" -")
            u2 = W.url_abs(BASE, h2)
            (alternativas if re.match(r"(?i)^ordenanza$|texto", t2) else anexos).append(u2)
        out.append((titulo, pub, ref, cat, principal, alternativas, anexos))
    return out


def main(enriquecer=False):
    if enriquecer:
        n = W.enriquecer_catalogo(CODIGO)
        print(f"{n} normas enriquecidas con alias por contenido")
        return
    normas, fallos = [], []
    idx = 0
    for ruta, cat_pagina in PAGINAS:
        htm = W.get(BASE + ruta).decode("utf-8", "replace")
        entradas = parsear(htm, cat_pagina)
        print(f"{ruta}: {len(entradas)} normas")
        for titulo, pub, ref, cat, principal, alternativas, anexos in entradas:
            idx += 1
            alias = alias_para(titulo)
            tn = norm(titulo)
            for rx, extra in EXTRAS:
                if re.search(rx, tn):
                    alias.extend(extra)
            n = {"id": f"{CODIGO}-{idx:03}", "titulo": titulo, "cat": cat, "ref": ref, "pub": pub,
                 "mod": "", "alias": W.uniq(alias), "url": principal, "formato": "pdf"}
            if alternativas:
                n["urls"] = alternativas
            if anexos:
                n["anexos"] = anexos
            normas.append(n)

    with cf.ThreadPoolExecutor(max_workers=2) as ex:
        res = dict(zip([n["id"] for n in normas], ex.map(lambda n: W.validar_pdf(n["url"]), normas)))
    buenas = []
    for n in normas:
        est, pags, cpp = res[n["id"]]
        if est in ("OK", "ESCANEADO"):
            print(f"✅ [{est:9}] {pags:3d}p {cpp:5d}c/p  {n['id']} {n['titulo'][:66]}")
            buenas.append(n)
        else:
            print(f"❌ [{est:12}]  {n['id']} {n['titulo'][:60]}  {n['url']}")
            fallos.append(n)
    meta = {"municipio": CODIGO, "nombre": "Guadalajara",
            "aliases": ["guadalajara", "ayuntamiento de guadalajara", "guadalajara capital",
                        "ciudad de guadalajara", "excmo ayuntamiento de guadalajara"],
            "fuente": "Ayuntamiento de Guadalajara (ordenanzas generales, fiscales y reglamentos consolidados, PDF oficial por norma)",
            "url": BASE + "/es/ayuntamiento/normativa/ordenanzas-generales/",
            "url_fiscales": BASE + "/es/ayuntamiento/normativa/ordenanzas-fiscales/",
            "url_reglamentos": BASE + "/es/ayuntamiento/normativa/reglamentos-y-estatutos/",
            "actualizado": "2026-09",
            "nota": "`url` es el texto de la norma; `urls` versiones alternativas (texto limpio frente al "
                    "íntegro del BOP) y `anexos` cuadros sancionadores, coeficientes o rectificaciones."}
    fp = W.escribir_catalogo(CODIGO, meta, buenas)
    print(f"\n{len(buenas)} normas escritas en {fp}" + (f" · {len(fallos)} fallos" if fallos else ""))


if __name__ == "__main__":
    main(enriquecer="--enriquecer" in sys.argv)
