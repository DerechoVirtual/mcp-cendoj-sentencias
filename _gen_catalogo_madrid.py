# -*- coding: utf-8 -*-
"""
Genera ordenanzas_data/madrid.json a partir del ePub del Codigo electronico
AEBOE n.329 "Normativa del Ayuntamiento de Madrid" (consolidado por la AEBOE).

Script OFFLINE (excluido del deploy por el patron `_*` de .vercelignore).
Re-ejecutar cuando la AEBOE actualice el codigo (dc:date del content.opf):

    python _gen_catalogo_madrid.py [ruta_epub_local]

Sin argumento, descarga el ePub (~24 MB) a un temporal y lo procesa.
"""
import io
import json
import os
import re
import sys
import html as _html
import urllib.request
import zipfile
import xml.etree.ElementTree as ET

CODIGO_ID = 329
EPUB_FICH = "329_Normativa_del_Ayuntamiento_de_Madrid.epub"
EPUB_URL = f"https://www.boe.es/biblioteca_juridica/codigos/abrir_epub.php?fich={EPUB_FICH}"
URL_CODIGO = f"https://www.boe.es/biblioteca_juridica/codigos/codigo.php?id={CODIGO_ID}"

_HERE = os.path.dirname(os.path.abspath(__file__))
SALIDA = os.path.join(_HERE, "ordenanzas_data", "madrid.json")

# Alias de materia curados a mano (lo que un abogado escribiria), por id de norma.
# El motor los machea normalizados (sin tildes), asi que van sin tildes.
ALIAS = {
    "conso-38597": ["pleno", "reglamento del pleno"],
    "conso-38598": ["gobierno y administracion", "reglamento organico del gobierno"],
    "conso-48582": ["distritos", "juntas de distrito", "junta municipal de distrito"],
    "conso-38600": ["tribunal economico administrativo", "teamm",
                    "reclamacion economico administrativa"],
    "conso-38602": ["transparencia", "acceso a la informacion publica"],
    "conso-38603": ["administracion electronica", "atencion a la ciudadania",
                    "sede electronica", "registro electronico", "cita previa"],
    "conso-38604": ["movilidad", "zbe", "zona de bajas emisiones", "madrid central",
                    "trafico", "circulacion", "estacionamiento", "aparcamiento",
                    "ser", "servicio de estacionamiento regulado", "zona azul", "zona verde",
                    "bicicleta", "carril bici", "patinete", "vmp",
                    "vehiculo de movilidad personal", "peatones", "distintivo ambiental",
                    "multa de trafico", "grua", "moto", "motocicleta"],
    "conso-38605": ["taxi", "autotaxi", "eurotaxi", "licencia de taxi", "taximetro"],
    "conso-38606": ["vado", "vados", "paso de vehiculos", "paso de carruajes",
                    "entrada de vehiculos"],
    "conso-48128": ["calidad del aire", "emisiones", "calderas",
                    "contaminacion atmosferica", "sostenibilidad"],
    "conso-38607": ["medio ambiente urbano", "zonas verdes", "parques", "jardines",
                    "arbolado", "poda"],
    "conso-38608": ["evaluacion ambiental", "licencia ambiental", "actividades clasificadas"],
    "conso-38609": ["agua", "uso eficiente del agua", "riego", "ahorro de agua"],
    "conso-38610": ["ruido", "ruidos", "contaminacion acustica", "acustica", "termica",
                    "sonora", "insonorizacion", "decibelios", "molestias por ruido"],
    "conso-61411": ["limpieza", "residuos", "basura", "basuras", "contenedores",
                    "recogida de residuos", "economia circular", "pintadas", "grafitis",
                    "escombros", "punto limpio"],
    "conso-38612": ["mobiliario urbano", "marquesinas", "papeleras"],
    "conso-38614": ["prensa gratuita", "distribucion de prensa"],
    "conso-38615": ["quiosco de prensa", "quioscos de prensa", "kiosco de prensa"],
    "conso-66304": ["terraza", "terrazas", "veladores", "quioscos de hosteleria",
                    "mesas y sillas", "hosteleria", "restauracion", "terraza de bar",
                    "horario de terrazas"],
    "conso-52494": ["licencia urbanistica", "licencias urbanisticas",
                    "declaracion responsable", "licencia de obras", "licencia de actividad",
                    "licencia de apertura", "obras", "cambio de uso"],
    "conso-52495": ["entidades colaboradoras", "ecu", "verificacion inspeccion y control"],
    "conso-38622": ["ite", "inspeccion tecnica de edificios", "conservacion de edificios",
                    "rehabilitacion", "ruina", "estado ruinoso", "deber de conservacion"],
    "conso-38617": ["publicidad exterior", "carteles", "rotulos", "lonas publicitarias",
                    "vallas publicitarias"],
    "conso-38616": ["obras en la via publica", "calas", "canalizaciones", "zanjas"],
    "conso-38619": ["denominacion de vias", "rotulacion de calles", "nombres de calles",
                    "callejero", "placas de calle"],
    "conso-38618": ["codigo identificativo de locales", "locales con puerta de calle",
                    "censo de locales"],
    "conso-38623": ["prestaciones economicas", "ayudas sociales", "servicios sociales",
                    "emergencia social"],
    "conso-61410": ["ayuda a domicilio", "sad", "servicio de ayuda a domicilio"],
    "conso-38625": ["escuelas infantiles", "guarderia", "guarderias", "educacion infantil"],
    "conso-38626": ["escuelas de musica", "musica y danza"],
    "conso-38628": ["participacion ciudadana", "consultas ciudadanas", "iniciativa popular"],
    "conso-48583": ["consejos de proximidad"],
    "conso-38629": ["mercados municipales", "mercado municipal"],
    "conso-38630": ["dinamizacion comercial", "actividades comerciales en dominio publico"],
    "conso-38631": ["venta ambulante", "mercadillo", "mercadillos", "puestos ambulantes",
                    "food truck"],
    "conso-38632": ["rastro", "el rastro", "venta en el rastro"],
    "conso-38633": ["consumo", "consumidores", "omic", "hojas de reclamaciones"],
    "conso-38634": ["salubridad", "salud publica", "piscinas", "tatuajes", "plagas",
                    "control sanitario"],
    "conso-38635": ["animales", "perros", "gatos", "mascotas", "tenencia de animales",
                    "ppp", "perros potencialmente peligrosos", "colonias felinas"],
    "conso-38637": ["iae", "impuesto sobre actividades economicas"],
    "conso-38638": ["ivtm", "impuesto de vehiculos", "impuesto de circulacion",
                    "vehiculos de traccion mecanica"],
    "conso-38640": ["plusvalia", "plusvalia municipal", "iivtnu",
                    "incremento de valor de los terrenos"],
    "conso-38641": ["ibi", "impuesto sobre bienes inmuebles", "contribucion"],
    "conso-50167": ["ordenanza fiscal general", "gestion recaudacion e inspeccion",
                    "recaudacion", "aplazamiento", "fraccionamiento",
                    "inspeccion tributaria", "tributos municipales"],
    "conso-63367": ["icio", "impuesto sobre construcciones",
                    "construcciones instalaciones y obras"],
    "conso-38642": ["subvenciones", "bases reguladoras de subvenciones"],
    "conso-38643": ["patrocinio", "patrocinios", "mecenazgo"],
}

_NCX = {"n": "http://www.daisy.org/z3986/2005/ncx/"}
_OPF = {"o": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}


def _texto_plano(frag: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", frag))).strip()


def _cabecera(xhtml: str) -> dict:
    """Extrae publicacion/referencia/ultima modificacion de la cabecera de una norma."""
    head = xhtml[:5000]
    pubs = [_texto_plano(p) for p in re.findall(r'<p class="pub"[^>]*>(.*?)</p>', head, re.S)]
    out = {"pub": "", "mod": "", "ref": ""}
    for p in pubs:
        if p.lower().startswith("referencia"):
            out["ref"] = p.split(":", 1)[1].strip()
        elif "ltima modificaci" in p:
            out["mod"] = p.split(":", 1)[1].strip()
        elif re.search(r"«\w+» núm\.\s*\d", p):
            out["pub"] = out["pub"] or p
    return out


def main():
    if len(sys.argv) > 1:
        ruta = sys.argv[1]
        print(f"Usando ePub local: {ruta}")
        data = open(ruta, "rb").read()
    else:
        print(f"Descargando {EPUB_URL} ...")
        req = urllib.request.Request(EPUB_URL, headers={"User-Agent": "jurisprudenciator-ordenanzas/1.0"})
        data = urllib.request.urlopen(req, timeout=120).read()
        print(f"  {len(data)/1e6:.1f} MB")

    z = zipfile.ZipFile(io.BytesIO(data))
    nombres = z.namelist()

    # fecha de actualizacion del codigo (content.opf -> dc:date)
    opf = z.read(next(n for n in nombres if n.endswith(".opf"))).decode("utf-8", "replace")
    m = re.search(r"<dc:date>([\d-]+)</dc:date>", opf)
    actualizado = m.group(1) if m else ""

    root = ET.fromstring(z.read("OEBPS/toc.ncx"))
    normas, vistos = [], set()
    for cat in root.find("n:navMap", _NCX).findall("n:navPoint", _NCX):
        cat_txt = " ".join(cat.find("n:navLabel/n:text", _NCX).text.split())
        for np in cat.findall("n:navPoint", _NCX):
            titulo = " ".join(np.find("n:navLabel/n:text", _NCX).text.split())
            src = np.find("n:content", _NCX).get("src").split("#")[0]
            base = re.match(r"(conso-\d+)", src).group(1)
            if base in vistos:
                continue
            vistos.add(base)
            ficheros = sorted(n for n in nombres
                              if re.fullmatch(rf"OEBPS/{base}(_\d+)?\.xhtml", n))
            cab = _cabecera(z.read(ficheros[0]).decode("utf-8", "replace"))
            if base not in ALIAS:
                print(f"  AVISO: {base} ({titulo}) sin alias curados")
            normas.append({
                "id": base, "titulo": titulo, "cat": cat_txt,
                "ref": cab["ref"], "pub": cab["pub"], "mod": cab["mod"],
                "alias": ALIAS.get(base, []), "ficheros": ficheros,
            })

    catalogo = {
        "meta": {"codigo": CODIGO_ID, "titulo": "Normativa del Ayuntamiento de Madrid",
                 "actualizado": actualizado, "epub": EPUB_FICH,
                 "epub_url": EPUB_URL, "url": URL_CODIGO},
        "normas": normas,
    }
    os.makedirs(os.path.dirname(SALIDA), exist_ok=True)
    with open(SALIDA, "w", encoding="utf-8") as f:
        json.dump(catalogo, f, ensure_ascii=False, indent=1)
    huerf = [i for i in ALIAS if i not in vistos]
    if huerf:
        print(f"  AVISO: alias sin norma en el codigo: {huerf}")
    print(f"OK -> {SALIDA} ({len(normas)} normas, act. {actualizado}, "
          f"{os.path.getsize(SALIDA)/1024:.0f} KB)")


if __name__ == "__main__":
    main()
