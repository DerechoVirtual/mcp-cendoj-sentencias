# Brief: añadir una provincia (o una capital) al motor de ordenanzas de Jurisprudenciator

Trabajas en el repo del CONECTOR (clon fresco de GitHub `DerechoVirtual/mcp-cendoj-sentencias`, rama main)
que está en **`C:\Users\carlo\AppData\Local\Temp\claude\jpd\w`**. Python a usar (tiene fitz/pymupdf, httpx, mcp):

    "C:/Users/carlo/OneDrive/Documentos/antigravity/mcp-cendoj/.venv/Scripts/python.exe" -X utf8 -u script.py

(`-X utf8` es obligatorio: sin él Windows rompe las tildes.) Las claves de OCR (OPENAI_API_KEY / GEMINI_API_KEY)
viven en `~/.claude/.env`; cárgalas en tus scripts de prueba como hace `_test_fixes.py` (primeras líneas).

## Qué hay ya

* `bop_engine.py` = motor genérico de ordenanzas vía Boletín Oficial de la Provincia. El RANKING (tesauro de
  materias `_EXPANSION`, `_ranquear`, `_mejor`, `_mejor_fulltext`, `_mejor_verificado`, `_pasajes`, `_articulos`,
  mensaje honesto `_honesto`) es COMÚN. Solo cambia el BACKEND por "familia" de plataforma, que produce:
  (a) la lista de anuncios y (b) el texto de uno.
* Cada provincia tiene `ordenanzas_data/bop_<id>_config.json` (con `familia`, `base`, `nombre`, `activo`,
  `indice_desde`, `endpoints`, `nota`… = **la RECETA ya verificada en vivo el 27-jul-2026**) y
  `ordenanzas_data/bop_<id>_municipios.json` (mapa `{"Nombre municipio": "<valor de filtro>"}`).
  El motor las carga solas (`_cargar_provincias`) si `activo` no es `false`.
* **Backends EXTERNOS (contrato nuevo, úsalo):** una familia `X` se implementa en el fichero **`bop_X.py`**
  (en la raíz del repo) con DOS funciones y NADA más obligatorio:

  ```python
  import bop_engine as B          # helpers: B._norm, B._mnorm, B._familias(texto)[0] (términos raw),
                                  # B._consultas_materia(texto, idioma), B._pdf_bytes_texto(bytes, ocr=False),
                                  # B._html_a_texto(html), B._UA, B._SSL_NOVERIFY, B._rest_get, B._rest_post
  def buscar(prov, texto, filtro, rpp=40):
      """prov = id ('larioja'); cfg = B.PROVINCIAS[prov]; filtro = valor del mapa para ese municipio
      (o None). Devuelve [{"url":..., "titulo":..., "cve":..., "fecha":"dd/mm/aaaa", "orden":"aaaammdd", ...}]
      + cualquier clave privada que necesite texto() (id de anuncio, pdf, página...)."""
  def texto(prov, m):
      """(texto_plano, via) del anuncio m (un dict de buscar). via: 'html' | 'pdf' | 'pdf-dia' | ...
      Devuelve ("", "sin-texto") si no hay texto."""
  ```

  `bop_engine._buscar_raw` / `_texto` despachan a `bop_<familia>.py` automáticamente (importlib) cuando la
  familia no es una de las internas. **NO edites `bop_engine.py`, `ordenanzas_engine.py` ni `server_http.py`**:
  si necesitas algo de ellos, dilo en tu informe final.
* Semántica de dos flags del config que el motor respeta: `"fulltext": true` = el buscador ya ordena por
  relevancia y el motor se fía del orden (`_mejor_fulltext`); `"verifica_texto": true` = los títulos del boletín
  son genéricos y el motor LEE los mejores candidatos para elegir por contenido (`_mejor_verificado`, lee sin OCR).
  Sin flags: ranking por TÍTULO con el tesauro (lo normal). Si tu boletín devuelve orden por fecha, NO pongas fulltext.
* Ejemplos de backends internos para copiar el estilo: `_caceres_buscar/_caceres_texto` (REST JSON),
  `_toledo_*` (Solr), `_valladolid_*` (GET), `_girona_*` (JSF), `_cadiz_*` (OpenCms + PDF del día con #page),
  `_acoruna_*` (POST + filtro local), `_madrid_*` (índice empaquetado en `ordenanzas_data/madrid_indice/`).
* Sondas de la sesión del 27-jul (solo lectura, NO las copies al repo): en
  `C:\Users\carlo\OneDrive\Documentos\antigravity\mcp-cendoj\` hay `_probe_<prov>*.py`, `_bu_lib.py`, `_sa_lib.py`…
  Léelas: ya resolvieron tokens, ViewStates, encodings y parseos.

## Patrón "capital vía su web propia" (Ceuta, Cuenca, Guadalajara, Palencia, Zamora, Ávila, Segovia)

Cuando el BOP no sirve pero el ayuntamiento publica sus ordenanzas CONSOLIDADAS en su web:
1. Genera `ordenanzas_data/<codigo>.json` con `{"meta": {"municipio": "<codigo>", "nombre": "Cuenca",
   "aliases": ["cuenca", "ayuntamiento de cuenca", "cuenca capital"], "fuente": "...", "url": "..."},
   "normas": [{"id": "<codigo>-...", "titulo": "...", "cat": "Ordenanzas|Reglamentos|Fiscales", "ref": "",
   "pub": "", "mod": "", "alias": ["terrazas", ...], "url": "https://...pdf", "formato": "pdf|html"}]}`.
   Mira `ordenanzas_data/leon_capital.json` y `_gen_leon_capital.py` como modelo (alias: `_gen_comun.alias_para`).
   Con `meta.aliases` + `meta.nombre` el motor lo registra SOLO (`_registrar_catalogos_auto`), sin tocar código.
2. Empaqueta el texto: `python -X utf8 _fill_textos.py <codigo> --workers 3` → escribe
   `ordenanzas_data/<codigo>_textos/<id>.txt` y marca `texto` en cada norma (lectura local, 0 red).
3. Comprueba con `ordenanzas_engine.buscar("Cuenca", "terrazas")` y `leer("Cuenca", "<id>", parrafos=3, terminos="terrazas")`.

## Lo que tienes que entregar por provincia

1. `bop_<familia>.py` (o el catálogo + textos si es capital vía web) siguiendo la receta del config/nota y las sondas.
2. `"activo": true` en su `bop_<id>_config.json` (y añade/ajusta `nombre`, `indice_desde`, `idioma` si toca).
3. **Colisiones de enrutado** que diga la nota (p.ej. añadir municipios al `excluir` de otra provincia): hazlas.
   Comprueba después con `bop_engine.provincia_de("<municipio>")` que cada municipio grande de tu provincia
   resuelve a la tuya y que no has robado ninguno a otra.
4. Banco `_test_bop_<familia>.py` con **≥10 casos reales** (primero los municipios de más de 50.000 hab. que
   se indican, después otros), que use el mismo flujo que el chat: `ordenanzas_engine.buscar(muni, materia, 6)`
   y `ordenanzas_engine.leer(muni, materia, "", 3, materia, 0)`; éxito = la lectura empieza por `【`, contiene
   texto literal de la materia y NO es un error. Materias: terrazas/veladores, residuos/limpieza, ruido,
   animales, IBI/ordenanza fiscal, convivencia, venta ambulante, agua… Si una materia no existe en ese
   boletín, el motor debe responder HONESTO (“No encuentro…”), nunca “Error”/“PDF sin texto”.
   Exige: ≥ 90 % de casos OK, **mediana < 5 s y máximo < 15 s** por caso (desde este PC).
5. Informe final (texto, en español): qué implementaste, resultados del banco (n/N, mediana, máximo), trampas
   descubiertas, y qué cambios (si alguno) harían falta en los ficheros compartidos.

## Reglas duras

* NADA de despliegues, `vercel`, `git push`, ni tocar producción (`mcp.jurisprudenciator…`): solo local.
* No borres archivos de Carlos. No edites nada fuera del clon `jpd\w` (las sondas de OneDrive son de solo lectura).
* Sé educado con los boletines: nada de ráfagas (≤ 3-4 peticiones en paralelo), reintenta con espera; algunos
  WAF banean la IP 15 min (Asturias) → si te bloquean, para y espera, no insistas.
* Texto que ve el abogado en castellano, sin nombrar “CENDOJ”. Errores blandos como texto, nunca excepciones.
* Timeouts ≤ 25 s por petición: el conector corre en Vercel (60 s de tope por llamada).
