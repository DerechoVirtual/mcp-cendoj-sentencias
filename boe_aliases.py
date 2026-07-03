# -*- coding: utf-8 -*-
"""Alias de leyes españolas -> identificador BOE. TODOS los IDs verificados
contra la API real del BOE (/metadatos) el 2026-07-01. Ver verificar_alias.py.

Formato: (lista_de_alias, id_boe, nombre_corto).
El servidor normaliza (minúsculas, sin acentos, sin puntuación) ambos lados,
así que da igual acentos/mayúsculas/puntos en el alias o en la consulta."""

LEYES = [
    # ---- Constitución y códigos ----
    (["CE", "Constitución", "Constitución Española", "constitucion", "carta magna"],
     "BOE-A-1978-31229", "Constitución Española"),
    (["CC", "Código Civil", "cod civil", "codigo civil"],
     "BOE-A-1889-4763", "Código Civil"),
    (["CCom", "Código de Comercio", "codigo de comercio", "cod comercio"],
     "BOE-A-1885-6627", "Código de Comercio"),
    (["CP", "Código Penal", "codigo penal", "LO 10/1995"],
     "BOE-A-1995-25444", "Código Penal (LO 10/1995)"),
    # ---- Procesales ----
    (["LEC", "Ley de Enjuiciamiento Civil", "enjuiciamiento civil", "Ley 1/2000"],
     "BOE-A-2000-323", "LEC (Ley 1/2000)"),
    (["LECrim", "LECr", "Ley de Enjuiciamiento Criminal", "enjuiciamiento criminal"],
     "BOE-A-1882-6036", "LECrim"),
    (["LJCA", "Ley de la Jurisdicción Contencioso-administrativa", "contencioso administrativo",
      "jurisdiccion contenciosa", "Ley 29/1998"],
     "BOE-A-1998-16718", "LJCA (Ley 29/1998)"),
    (["LRJS", "LPL", "Ley reguladora de la Jurisdicción Social", "jurisdiccion social",
      "ley social", "Ley 36/2011"],
     "BOE-A-2011-15936", "LRJS (Ley 36/2011)"),
    (["LJV", "Ley de Jurisdicción Voluntaria", "jurisdiccion voluntaria", "Ley 15/2015"],
     "BOE-A-2015-7391", "Ley Jurisdicción Voluntaria (15/2015)"),
    (["LOTC", "Ley Orgánica del Tribunal Constitucional", "tribunal constitucional", "LO 2/1979"],
     "BOE-A-1979-23709", "LOTC (LO 2/1979)"),
    (["LODH", "habeas corpus", "ley de habeas corpus", "LO 6/1984"],
     "BOE-A-1984-11620", "LO Habeas Corpus (6/1984)"),
    # ---- Organización judicial ----
    (["LOPJ", "Ley Orgánica del Poder Judicial", "poder judicial", "LO 6/1985"],
     "BOE-A-1985-12666", "LOPJ (LO 6/1985)"),
    (["LOTCu", "Tribunal de Cuentas", "ley del tribunal de cuentas", "LO 2/1982"],
     "BOE-A-1982-11584", "LO Tribunal de Cuentas (2/1982)"),
    # ---- Administrativo ----
    (["LPAC", "Ley de Procedimiento Administrativo Común", "procedimiento administrativo",
      "Ley 39/2015"],
     "BOE-A-2015-10565", "LPAC (Ley 39/2015)"),
    (["LRJSP", "Régimen Jurídico del Sector Público", "regimen juridico sector publico",
      "Ley 40/2015"],
     "BOE-A-2015-10566", "LRJSP (Ley 40/2015)"),
    (["LCSP", "Ley de Contratos del Sector Público", "contratos del sector publico",
      "contratos publicos", "Ley 9/2017"],
     "BOE-A-2017-12902", "LCSP (Ley 9/2017)"),
    (["LEF", "Ley de Expropiación Forzosa", "expropiacion forzosa", "expropiacion"],
     "BOE-A-1954-15431", "Ley Expropiación Forzosa (1954)"),
    (["LTBG", "LTAIBG", "Ley de Transparencia", "transparencia", "Ley 19/2013"],
     "BOE-A-2013-12887", "Ley Transparencia (19/2013)"),
    # ---- Tributario ----
    (["LGT", "Ley General Tributaria", "general tributaria", "Ley 58/2003"],
     "BOE-A-2003-23186", "LGT (Ley 58/2003)"),
    (["LIVA", "Ley del IVA", "impuesto sobre el valor añadido", "iva", "Ley 37/1992"],
     "BOE-A-1992-28740", "LIVA (Ley 37/1992)"),
    (["LIRPF", "Ley del IRPF", "impuesto sobre la renta", "irpf", "Ley 35/2006"],
     "BOE-A-2006-20764", "LIRPF (Ley 35/2006)"),
    (["LIS", "Ley del Impuesto sobre Sociedades", "impuesto sobre sociedades", "Ley 27/2014"],
     "BOE-A-2014-12328", "LIS (Ley 27/2014)"),
    (["LGP", "Ley General Presupuestaria", "general presupuestaria", "Ley 47/2003"],
     "BOE-A-2003-21614", "LGP (Ley 47/2003)"),
    # ---- Laboral / social ----
    (["ET", "Estatuto de los Trabajadores", "estatuto de los trabajadores", "RDL 2/2015"],
     "BOE-A-2015-11430", "Estatuto Trabajadores (RDL 2/2015)"),
    (["LETA", "Estatuto del Trabajo Autónomo", "trabajo autonomo", "Ley 20/2007"],
     "BOE-A-2007-13409", "Estatuto Trabajo Autónomo (20/2007)"),
    (["LGSS", "Ley General de la Seguridad Social", "seguridad social", "RDL 8/2015"],
     "BOE-A-2015-11724", "LGSS (RDL 8/2015)"),
    (["LPRL", "Ley de Prevención de Riesgos Laborales", "prevencion de riesgos laborales",
      "riesgos laborales", "Ley 31/1995"],
     "BOE-A-1995-24292", "LPRL (Ley 31/1995)"),
    (["LOLS", "Ley Orgánica de Libertad Sindical", "libertad sindical", "LO 11/1985"],
     "BOE-A-1985-16660", "LO Libertad Sindical (11/1985)"),
    # ---- Mercantil ----
    (["LSC", "Ley de Sociedades de Capital", "sociedades de capital", "RDL 1/2010"],
     "BOE-A-2010-10544", "LSC (RDL 1/2010)"),
    (["LC", "TRLC", "Ley Concursal", "ley concursal", "concursal", "RDL 1/2020"],
     "BOE-A-2020-4859", "Ley Concursal (RDL 1/2020)"),
    (["LCCh", "Ley Cambiaria y del Cheque", "cambiaria y del cheque", "cambiaria", "Ley 19/1985"],
     "BOE-A-1985-14880", "Ley Cambiaria y del Cheque (19/1985)"),
    (["LCS", "Ley de Contrato de Seguro", "contrato de seguro", "Ley 50/1980"],
     "BOE-A-1980-22501", "Ley Contrato de Seguro (50/1980)"),
    (["LDC", "Ley de Defensa de la Competencia", "defensa de la competencia", "Ley 15/2007"],
     "BOE-A-2007-12946", "Ley Defensa Competencia (15/2007)"),
    (["LSSICE", "LSSI", "servicios de la sociedad de la información", "comercio electronico",
      "Ley 34/2002"],
     "BOE-A-2002-13758", "LSSI-CE (Ley 34/2002)"),
    # ---- Propiedad ----
    (["LH", "Ley Hipotecaria", "hipotecaria"],
     "BOE-A-1946-2453", "Ley Hipotecaria (1946)"),
    (["LPH", "Ley de Propiedad Horizontal", "propiedad horizontal", "Ley 49/1960"],
     "BOE-A-1960-10906", "LPH (Ley 49/1960)"),
    (["LAU", "Ley de Arrendamientos Urbanos", "arrendamientos urbanos", "Ley 29/1994"],
     "BOE-A-1994-26003", "LAU (Ley 29/1994)"),
    (["LAR", "Ley de Arrendamientos Rústicos", "arrendamientos rusticos", "Ley 49/2003"],
     "BOE-A-2003-21616", "LAR (Ley 49/2003)"),
    (["TRLPI", "LPI", "Ley de Propiedad Intelectual", "propiedad intelectual", "RDL 1/1996"],
     "BOE-A-1996-8930", "TRLPI (RDL 1/1996)"),
    (["LP", "Ley de Patentes", "patentes", "Ley 24/2015"],
     "BOE-A-2015-8328", "Ley de Patentes (24/2015)"),
    (["LM", "Ley de Marcas", "marcas", "Ley 17/2001"],
     "BOE-A-2001-23093", "Ley de Marcas (17/2001)"),
    (["TRLSRU", "Ley del Suelo", "suelo y rehabilitacion urbana", "ley del suelo", "RDL 7/2015"],
     "BOE-A-2015-11723", "TR Ley del Suelo (RDL 7/2015)"),
    # ---- Consumidores ----
    (["TRLGDCU", "LGDCU", "Ley de Consumidores y Usuarios", "consumidores y usuarios",
      "defensa de los consumidores", "RDL 1/2007"],
     "BOE-A-2007-20555", "TRLGDCU (RDL 1/2007)"),
    # ---- Protección de datos / derechos ----
    (["LOPDGDD", "LOPD", "Ley de Protección de Datos", "proteccion de datos", "LO 3/2018"],
     "BOE-A-2018-16673", "LOPDGDD (LO 3/2018)"),
    (["LOIgualdad", "Ley de Igualdad", "igualdad efectiva", "LO 3/2007"],
     "BOE-A-2007-6115", "LO Igualdad (3/2007)"),
    (["LOVG", "LIVG", "Ley de Violencia de Género", "violencia de genero", "LO 1/2004"],
     "BOE-A-2004-21760", "LO Violencia de Género (1/2004)"),
    (["LOPJM", "Ley de Protección Jurídica del Menor", "proteccion juridica del menor",
      "proteccion del menor", "LO 1/1996"],
     "BOE-A-1996-1069", "LO Protección del Menor (1/1996)"),
    (["LOEX", "Ley de Extranjería", "extranjeria", "LO 4/2000"],
     "BOE-A-2000-544", "LO Extranjería (4/2000)"),
    # ---- Registro civil ----
    (["LRC", "Ley del Registro Civil", "registro civil", "Ley 20/2011"],
     "BOE-A-2011-12628", "Ley Registro Civil (20/2011)"),
    # ---- Seguridad / electoral / otras ----
    (["LOREG", "Ley Electoral", "régimen electoral general", "regimen electoral", "LO 5/1985"],
     "BOE-A-1985-11672", "LOREG (LO 5/1985)"),
    (["LOFCS", "Fuerzas y Cuerpos de Seguridad", "fuerzas y cuerpos de seguridad", "LO 2/1986"],
     "BOE-A-1986-6859", "LO Fuerzas y Cuerpos Seguridad (2/1986)"),
    (["LOSC", "Ley de Seguridad Ciudadana", "seguridad ciudadana", "ley mordaza", "LO 4/2015"],
     "BOE-A-2015-3442", "LO Seguridad Ciudadana (4/2015)"),
    (["LSV", "Ley de Tráfico", "trafico", "seguridad vial", "RDL 6/2015"],
     "BOE-A-2015-11722", "TR Ley Tráfico (RDL 6/2015)"),
    (["LOTT", "Ordenación de los Transportes Terrestres", "transportes terrestres", "Ley 16/1987"],
     "BOE-A-1987-17803", "LOTT (Ley 16/1987)"),
    # ---- Educación ----
    (["LOE", "LOMLOE", "Ley de Educación", "ley educacion", "LO 2/2006"],
     "BOE-A-2006-7899", "LO Educación (2/2006)"),
    (["LOSU", "Ley del Sistema Universitario", "sistema universitario", "LO 2/2023"],
     "BOE-A-2023-7500", "LO Sistema Universitario (2/2023)"),
    # ---- Eficiencia Justicia 2025 ----
    (["LO 1/2025", "Ley de Eficiencia Judicial", "eficiencia del servicio publico de justicia",
      "eficiencia justicia", "medidas de eficiencia"],
     "BOE-A-2025-76", "LO 1/2025 (Eficiencia Justicia)"),
]

# --- índices derivados (los rellena el servidor tras normalizar) ---
def build_index(norm):
    """Devuelve dict {alias_normalizado: (id, nombre_corto)}."""
    idx = {}
    for aliases, bid, nombre in LEYES:
        for a in aliases:
            idx[norm(a)] = (bid, nombre)
    return idx
