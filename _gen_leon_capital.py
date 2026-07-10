# -*- coding: utf-8 -*-
"""Genera ordenanzas_data/leon_capital.json — catálogo curado de las ordenanzas
CONSOLIDADAS del Ayuntamiento de León capital, publicadas como PDF directo en
aytoleon.es (mucho mejor que el BOP, que solo indexa 2025+). Valida cada URL en
vivo (200 + PDF + capa de texto) antes de escribir. Offline/_gen (no se despliega)."""
import concurrent.futures as cf
import json
import os
import re
import sys
import urllib.parse
import urllib.request

import fitz

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bop_engine as _bop           # OCR reutilizable (gpt-4o-mini + Gemini)
import ordenanzas_engine as _oe     # _reparar_parrafos_pdf
TEXTOS = os.path.join(HERE, "ordenanzas_data", "leon_capital_textos")
OCR_MAX_PAG = 26                    # cap de OCR para PDFs escaneados enormes (articulado)
BASE = "https://www.aytoleon.es"
DIRG = "/es/tu-ayuntamiento/corporación/secretaria/Ordenanzas Generales/"
DIRI = "/es/tu-ayuntamiento/corporación/secretaria/Ordenanzas Impuestos/"
DIRT = "/es/tu-ayuntamiento/corporación/secretaria/Ordenanzas Tasas/"
DIRR = "/es/tu-ayuntamiento/corporación/secretaria/Reglamentos/"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# (dir, nombre_de_fichero_EXACTO.pdf, titulo, cat, [alias...])
NORMAS = [
 (DIRG, "ORDENANZA REGULADORA DEL SERVICIO DE TAXI EN EL TÉRMINO MUNICIPAL DE LEÓN.pdf",
  "Ordenanza reguladora del servicio de taxi", "Transporte",
  ["taxi", "autotaxi", "licencia de taxi", "auto-taxi", "vtc", "servicio de taxi"]),
 (DIRG, "TRÁFICO-ORDENANZA ORA.pdf",
  "Ordenanza reguladora de la ORA (zona azul)", "Tráfico",
  ["ora", "zona azul", "estacionamiento regulado", "aparcamiento regulado", "estacionamiento limitado"]),
 (DIRG, "20251201 Ordenanza de la Inspección Técnica de Edificos.pdf",
  "Ordenanza de la Inspección Técnica de Edificios (ITE)", "Urbanismo",
  ["ite", "inspeccion tecnica de edificios", "conservacion de edificios", "deber de conservacion"]),
 (DIRG, "20251016 Ordenanza Movilidad de la Ciudad de León.pdf",
  "Ordenanza de Movilidad de la Ciudad de León", "Movilidad",
  ["movilidad", "trafico", "circulacion", "zbe", "zona de bajas emisiones", "patinete", "vmp",
   "vehiculos de movilidad personal", "bicicleta", "estacionamiento", "emisiones"]),
 (DIRG, "20251008, Ordenanza municipal reguladora de vados para el acceso de vehículos en el municipio de León.pdf",
  "Ordenanza reguladora de vados para el acceso de vehículos", "Vía pública",
  ["vado", "vados", "entrada de vehiculos", "paso de carruajes", "reserva de aparcamiento"]),
 (DIRG, "20250911_Ordenanza_sobre_protección_de_la_convivencia_ciudadana.pdf",
  "Ordenanza sobre protección de la convivencia ciudadana y prevención de conductas antisociales", "Convivencia",
  ["convivencia", "civismo", "botellon", "conductas antisociales", "conductas incivicas",
   "espacio publico", "ordenanza civica", "grafiti", "gamberrismo"]),
 (DIRG, "ORDENANZA REGULADORA DE LAS AYUDAS AL ESTUDIO.pdf",
  "Ordenanza reguladora de las ayudas al estudio", "Ayudas",
  ["ayudas al estudio", "becas", "beca de estudio"]),
 (DIRG, "ORDENANZA REGULADORA DE LAS AYUDAS A LA NATALIDAD.pdf",
  "Ordenanza reguladora de las ayudas a la natalidad", "Ayudas",
  ["natalidad", "fomento de la natalidad", "ayuda por nacimiento", "cheque bebe"]),
 (DIRG, "MEDIO AMBIENTE - ORDENANZA REGULADORA DE LA LIMPIEZA EN ESPACIOS PÚBLICOS Y PRIVADOS RESIDUOS Y ECONOMÍA CIRCULAR .pdf",
  "Ordenanza reguladora de la limpieza en espacios públicos y privados, residuos y economía circular", "Medio Ambiente",
  ["limpieza", "residuos", "basura", "recogida de residuos", "recogida de basura", "economia circular",
   "higiene urbana", "limpieza viaria", "solidos urbanos", "reciclaje", "punto limpio"]),
 (DIRG, "ORDENANZA REGULADORA DEL ESTACIONAMIENTO Y PERNOCTA DE VEHÍCULOS AUTOCARAVANAS.pdf",
  "Ordenanza reguladora del estacionamiento y pernocta de autocaravanas", "Movilidad",
  ["autocaravana", "caravana", "pernocta", "estacionamiento de caravanas", "camper"]),
 (DIRG, "ORDENANZA MUNICIPAL DE TERRAZAS Y ELEMENTOS AUXILIARES DE HOSTELERÍA Y RESTAURACIÓN EN EL MUNICIPIO DE LEÓN.pdf",
  "Ordenanza municipal de terrazas y elementos auxiliares de hostelería y restauración", "Vía pública",
  ["terraza", "terrazas", "velador", "veladores", "mesas y sillas", "hosteleria", "restauracion",
   "ocupacion de via publica"]),
 (DIRG, "ORDENANZA AYUDAS TASAS MATRICULAS ESTUDIOS DE GRADO Y DE CICLO FORMATIVO SUPERIOR.pdf",
  "Ordenanza de ayudas a tasas de matrículas de grado y ciclo formativo superior", "Ayudas",
  ["ayudas matricula", "matricula", "grado", "universidad", "ciclo formativo"]),
 (DIRG, "ORDENANZA PARA LA LUCHA CONTRA LA PROSTITUCIÓN Y LA TRATA CON FINES  DE EXPLOTACIÓN SEXUAL EN EL MUNICIPIO DE LEÓN.pdf",
  "Ordenanza para la lucha contra la prostitución y la trata con fines de explotación sexual", "Convivencia",
  ["prostitucion", "trata", "explotacion sexual"]),
 (DIRG, "ORDENANZA REGULADORA DEL SISTEMA DE PRÉSTAMO DE BICICLETAS 'LEÓN.pdf",
  "Ordenanza reguladora del sistema de préstamo de bicicletas", "Movilidad",
  ["bicicleta publica", "prestamo de bicicletas", "bicileon", "bici electrica", "servicio de bicicletas"]),
 (DIRG, "TRÁFICO-ORDENANZA DE CIRCULACIÓN Y SEGURIDAD VIAL DE PEATONES Y CICLISTAS.pdf",
  "Ordenanza de circulación y seguridad vial de peatones y ciclistas", "Tráfico",
  ["peatones", "ciclistas", "seguridad vial", "circulacion de peatones"]),
 (DIRG, "TRÁFICO-ORDENANZA DE TRAFICO Y SEGURIDAD VIAL.pdf",
  "Ordenanza de tráfico y seguridad vial", "Tráfico",
  ["trafico", "seguridad vial", "circulacion", "multa de trafico", "sancion de trafico"]),
 (DIRG, "TRÁFICO-ORDENANZA ESPECIAL DE ESTACIONAMIENTO PARA PERSONAS CON DISCAPACIDAD.pdf",
  "Ordenanza especial de estacionamiento para personas con discapacidad", "Tráfico",
  ["discapacidad", "tarjeta de estacionamiento", "movilidad reducida", "pmr", "aparcamiento discapacitados"]),
 (DIRG, "TRÁFICO-ORDENANZA SOBRE LA REGULACION DEL TRAFICO EN EL CASCO HISTORICO.pdf",
  "Ordenanza sobre la regulación del tráfico en el casco histórico", "Tráfico",
  ["casco historico", "casco antiguo", "trafico centro", "acceso casco"]),
 (DIRG, "MEDIO AMBIENTE - ORDENANZA MUNICIPAL DE PARQUES Y JARDINES.pdf",
  "Ordenanza municipal de parques y jardines", "Medio Ambiente",
  ["parques", "jardines", "zonas verdes", "arbolado", "espacios verdes", "areas de juego"]),
 (DIRG, "MEDIO AMBIENTE - ORDENANZA MUNICIPAL REGULADORA DE LA INSTALACIÓN Y FUNCIONAMIENTO DE INFRAESTRUCTURAS DE RADIOCOMUNICACIÓN.pdf",
  "Ordenanza reguladora de la instalación y funcionamiento de infraestructuras de radiocomunicación", "Medio Ambiente",
  ["antenas", "radiocomunicacion", "telefonia movil", "infraestructuras de telecomunicaciones", "estaciones base"]),
 (DIRG, "URBANISMO- ORDENANZA REGULADORA DE LA TRAMITACIÓN DE LAS LICENCIAS DE PRIMERA UTILIZACIÓN.pdf",
  "Ordenanza reguladora de la tramitación de las licencias de primera utilización", "Urbanismo",
  ["licencia de primera ocupacion", "primera utilizacion", "primera ocupacion", "cedula de habitabilidad"]),
 (DIRG, "MEDIO AMBIENTE - ORDENANZA MUNICIPAL SOBRE LA PROTECCIÓN DEL MEDIO AMBIENTE CONTRA LA EMISIÓN DE RUIDOS Y VIBRACIONES.pdf",
  "Ordenanza municipal sobre la protección del medio ambiente contra la emisión de ruidos y vibraciones", "Medio Ambiente",
  ["medio ambiente", "ruido", "ruidos", "vibraciones", "contaminacion acustica", "acustica",
   "proteccion del medio ambiente", "molestias por ruido", "aislamiento acustico"]),
 (DIRG, "MEDIO AMBIENTE - ORDENANZA REGULADORA DE LA PUBLICIDAD EXTERIOR MEDIANTE CARTELES, CARTELERAS O VALLAS PUBLICITARIAS.pdf",
  "Ordenanza reguladora de la publicidad exterior mediante carteles, carteleras o vallas publicitarias", "Medio Ambiente",
  ["publicidad", "publicidad exterior", "carteles", "vallas", "carteleras", "rotulos"]),
 (DIRG, "ORDENANZA ORDENADORA DISTANCIA ESTABLECIMIENTOS BEBIDAS ALCOHÓLICAS.pdf",
  "Ordenanza ordenadora de la distancia de establecimientos de bebidas alcohólicas", "Convivencia",
  ["alcohol", "bebidas alcoholicas", "distancia de locales", "establecimientos de bebidas"]),
 (DIRG, "ORDENANZA DE TRANSPARENCIA Y ACCESO A LA INFORMACIÓN.pdf",
  "Ordenanza de transparencia y acceso a la información", "Gobierno abierto",
  ["transparencia", "acceso a la informacion", "buen gobierno", "gobierno abierto", "datos abiertos"]),
 (DIRG, "ORDENANZA MUNICIPAL DE HUERTOS.pdf",
  "Ordenanza municipal de huertos", "Medio Ambiente",
  ["huertos", "huertos urbanos", "huertos municipales", "huerto ecologico"]),
 # ---- impuestos
 (DIRI, "ORDENANZA FISCAL REGULADORA DEL IMPUESTO SOBRE EL INCREMENTO DE VALOR DE LOS TERRENOS DE NATURALEZA URBANA.pdf",
  "Ordenanza fiscal del impuesto sobre el incremento de valor de los terrenos de naturaleza urbana (plusvalía)", "Impuestos",
  ["plusvalia", "incremento de valor", "iivtnu", "terrenos de naturaleza urbana", "impuesto de plusvalia"]),
 (DIRI, "ORDENANZA FISCAL REGULADORA DEL IMPUESTO SOBRE CONSTRUCCIONES, INSTALACIONES Y OBRAS..pdf",
  "Ordenanza fiscal del impuesto sobre construcciones, instalaciones y obras (ICIO)", "Impuestos",
  ["icio", "construcciones instalaciones y obras", "obras", "impuesto de obras", "licencia de obras"]),
 (DIRI, "ORDENANZA FISCAL REGULADORA DEL IMPUESTO SOBRE BIENES INMUEBLES.pdf",
  "Ordenanza fiscal del impuesto sobre bienes inmuebles (IBI)", "Impuestos",
  ["ibi", "bienes inmuebles", "impuesto sobre bienes inmuebles", "contribucion"]),
 (DIRI, "ORDENANZA FISCAL REGULADORA DEL IMPUESTO SOBRE ACTIVIDADES ECONOMICAS.pdf",
  "Ordenanza fiscal del impuesto sobre actividades económicas (IAE)", "Impuestos",
  ["iae", "actividades economicas", "impuesto de actividades economicas"]),
 (DIRI, "ORDENANZA FISCAL REGULADORA DEL IMPUESTO SOBRE VEHICULOS TRACCION MECANICA.pdf",
  "Ordenanza fiscal del impuesto sobre vehículos de tracción mecánica (IVTM)", "Impuestos",
  ["ivtm", "vehiculos de traccion mecanica", "impuesto de circulacion", "impuesto de vehiculos", "traccion mecanica"]),
 # ---- tasas / precios públicos (materias distintas)
 (DIRT, "TASAS POR TRATAMIENTO Y ELIMINACIÓN RESIDUOS.pdf",
  "Tasa por tratamiento y eliminación de residuos", "Tasas",
  ["tasa de residuos", "tratamiento de residuos", "eliminacion de residuos", "tasa de tratamiento"]),
 (DIRT, "TASAS POR RECOGIDA DE BASURAS  Y OTROS RESIDUOS SÓLIDOS URBANOS 2022.pdf",
  "Tasa por recogida de basuras y otros residuos sólidos urbanos", "Tasas",
  ["tasa de basura", "recogida de basuras", "tasa de recogida", "residuos solidos urbanos", "tasa de residuos domiciliaria"]),
 (DIRT, "TASAS POR RETIRADA DE VEHÍCULOS Y SU DEPÓSITO.pdf",
  "Tasa por retirada de vehículos y su depósito (grúa)", "Tasas",
  ["grua", "retirada de vehiculos", "deposito de vehiculos", "tasa de grua", "inmovilizacion"]),
 (DIRT, "TASAS POR EL SERVICIO DE RECOGIDA DE PERROS.pdf",
  "Tasa por el servicio de recogida de perros", "Tasas",
  ["recogida de perros", "perros", "animales", "perrera", "tasa de perros"]),
 (DIRT, "TASAS POR LA PRESTACIÓN DE SERVICIOS AMBIENTALES.pdf",
  "Tasa por la prestación de servicios ambientales", "Tasas",
  ["servicios ambientales", "tasa ambiental"]),
 (DIRT, "TASAS POR OCUPACION DE TERRENOS DE USO PUBLICO LOCAL CON MESAS Y SILLAS CON FINALIDAD LUCRATIVA.pdf",
  "Tasa por ocupación de terrenos de uso público con mesas y sillas (terrazas)", "Tasas",
  ["tasa de terrazas", "mesas y sillas", "ocupacion de terrenos", "tasa de veladores"]),
 (DIRT, "TASAS POR EL SERVICIO DE EXTINCION DE INCENDIOS.pdf",
  "Tasa por el servicio de extinción de incendios", "Tasas",
  ["extincion de incendios", "bomberos", "tasa de bomberos", "incendios"]),
 (DIRT, "TASAS POR EXPEDICIÓN DE DOCUMENTOS.pdf",
  "Tasa por expedición de documentos", "Tasas",
  ["expedicion de documentos", "compulsa", "certificados", "tasa de documentos"]),
 (DIRT, "TASAS POR INSTALACIÓN DE QUIOSCOS EN LA VÍA PÚBLICA.pdf",
  "Tasa por instalación de quioscos en la vía pública", "Tasas",
  ["quiosco", "kiosco", "quioscos"]),
 (DIRT, "TASAS POR PRESTACIÓN DE SERVICIOS Y APROVECHAMIENTOS ESPECIALES MERCADOS MUNICIPALES.pdf",
  "Tasa por prestación de servicios en mercados municipales", "Tasas",
  ["mercado", "mercados municipales", "puesto de mercado", "abastos"]),
 (DIRT, "TASAS POR PRESTACION DEL SERVICIO DE AYUDA A DOMICILIO.pdf",
  "Tasa por prestación del servicio de ayuda a domicilio", "Tasas",
  ["ayuda a domicilio", "sad", "servicios sociales", "dependencia"]),
 # ---- reglamentos
 (DIRR, "REGLAMENTO DE PARTICIPACIÓN CIUDADANA.pdf",
  "Reglamento de participación ciudadana", "Participación",
  ["participacion ciudadana", "consejos de participacion", "consulta ciudadana"]),
 (DIRR, "Reglamento del CONSEJO DE LAS MUJERES.pdf",
  "Reglamento del Consejo de las Mujeres", "Igualdad",
  ["consejo de las mujeres", "igualdad", "mujeres"]),
 (DIRR, "REGLAMENTO DE LA AGRUPACIÓN DE VOLUNTARIADO DE PROTECCIÓN CIVIL 20250409, Reglamento de la Agrupación de Voluntarios de Protección Civil.pdf",
  "Reglamento de la Agrupación de Voluntarios de Protección Civil", "Protección Civil",
  ["proteccion civil", "voluntariado", "agrupacion de voluntarios", "emergencias"]),
]


def _extraer_texto(doc):
    """Texto del PDF; si es escaneado (ratio<300), OCR de las primeras páginas."""
    directo = "\n".join(doc[i].get_text() for i in range(doc.page_count))
    ratio = len(directo) / max(1, doc.page_count)
    if ratio >= 300:
        return _oe._reparar_parrafos_pdf(directo), "texto"
    n = min(doc.page_count, OCR_MAX_PAG)   # escaneado: OCR del articulado (cap)
    pngs = [doc[i].get_pixmap(dpi=150).tobytes("png") for i in range(n)]
    import concurrent.futures as _cf
    with _cf.ThreadPoolExecutor(max_workers=4) as ex:
        pags = list(ex.map(_bop._ocr_pagina, pngs))
    txt = "\n".join(p for p in pags if p)
    nota = "" if doc.page_count <= OCR_MAX_PAG else \
        f"\n\n[Nota: documento escaneado de {doc.page_count} págs; se transcribió el articulado (primeras {n} págs). Anexos/planos no incluidos.]"
    return txt + nota, f"ocr({n}/{doc.page_count}p)"


def validar(n):
    d, fich, tit, cat, alias = n
    url = BASE + urllib.parse.quote(d + fich, safe=":/")
    ultimo = "?"
    for intento in range(4):   # el server tiene arranque de conexion flaky
        try:
            data = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15).read()
            if data[:5] != b"%PDF-":
                return (tit, url, cat, alias, "NO_PDF", "", 0)
            doc = fitz.open(stream=data, filetype="pdf")
            txt, via = _extraer_texto(doc)
            if len(txt) < 400:
                return (tit, url, cat, alias, "SIN_TEXTO", "", 0)
            return (tit, url, cat, alias, "OK", txt, via)
        except urllib.error.HTTPError as e:
            return (tit, url, cat, alias, f"HTTP {e.code}", "", 0)  # 404 = nombre malo
        except Exception as e:  # noqa: BLE001
            ultimo = type(e).__name__
    return (tit, url, cat, alias, f"ERR {ultimo}", "", 0)


def main():
    out = os.path.join(HERE, "ordenanzas_data", "leon_capital.json")
    os.makedirs(TEXTOS, exist_ok=True)
    previas = {}   # merge incremental: no perder entradas buenas por fallo transitorio
    if os.path.exists(out):
        for n in json.load(open(out, encoding="utf-8")).get("normas", []):
            previas[n["titulo"]] = n
    with cf.ThreadPoolExecutor(max_workers=3) as ex:
        res = list(ex.map(validar, NORMAS))
    normas, fallos = [], []
    for i, (tit, url, cat, alias, estado, txt, via) in enumerate(res, 1):
        nid = f"leon-{i:03}"
        if estado == "OK":
            with open(os.path.join(TEXTOS, nid + ".txt"), "w", encoding="utf-8") as f:
                f.write(txt)
            print(f"✅ [{via:12}] {len(txt):7} ch  {tit[:58]}")
            normas.append({"id": nid, "titulo": tit, "cat": cat, "ref": "",
                           "pub": "", "mod": "", "alias": alias, "url": url,
                           "formato": "pdf", "texto": nid + ".txt"})
        elif tit in previas:   # fallo transitorio: conservar la validada de una corrida previa
            print(f"♻️ [prev-OK    ] (mantengo validada + su texto) {tit[:56]}")
            normas.append(previas[tit])
        else:
            print(f"❌ [{estado:12}]          {tit[:58]}")
            fallos.append((tit, url, estado))
    cat = {"meta": {"municipio": "leon_capital", "nombre": "León",
                    "fuente": "Ayuntamiento de León (ordenanzas municipales consolidadas, PDF oficial por norma)",
                    "url": "https://www.aytoleon.es/es/tu-ayuntamiento/normativas/Paginas/default.aspx",
                    "textos_dir": "leon_capital_textos", "actualizado": "2026-07"},
           "normas": normas}
    out = os.path.join(HERE, "ordenanzas_data", "leon_capital.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(cat, f, ensure_ascii=False, indent=1)
    print(f"\n{len(normas)} normas OK escritas en {out}")
    if fallos:
        print(f"\n⚠️ {len(fallos)} FALLOS (revisar el nombre de fichero):")
        for tit, url, estado in fallos:
            print(f"   [{estado}] {tit}\n      {url}")


if __name__ == "__main__":
    main()
