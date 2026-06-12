# CENDOJ Sentencias — Servidor MCP

Busca y **descarga sentencias del CENDOJ** (el buscador oficial y **gratuito** de
jurisprudencia del Poder Judicial, `poderjudicial.es`) directamente desde
**Claude** (Desktop / Cowork). Te da el **PDF oficial + el texto íntegro + el ECLI
+ los metadatos** de cada resolución, listo para sacar fundamentos jurídicos y
extractos al momento.

> **El captcha lo resuelve la propia visión de Claude.** Cuando el CENDOJ muestra
> el captcha "Control Descargas masivas", el servidor te devuelve la imagen dentro
> del chat; Claude la lee, escribe los 5 caracteres y la descarga continúa sola.
> **Sin API keys, sin 2captcha, sin coste.**

## Qué puede hacer

- **Buscar** jurisprudencia por texto libre con todos los filtros del CENDOJ:
  base TS/AN, fechas, tipo de resolución, **jurisdicción** (CIVIL, PENAL…),
  **provincia/sede** (Valladolid, Alicante…) y **tipo de órgano** (Audiencia
  Provincial, TSJ, Juzgado de 1ª Instancia, Mercantil…).
- Ver de un vistazo **ROJ, ECLI, fecha, sala, ponente, nº de recurso y el RESUMEN**
  oficial de cada sentencia — sin descargar nada — para elegir **la mejor, no la #1**.
- **Localizar por cita**: buscar una resolución por su **ECLI o ROJ exacto**
  (verificar una cita en segundos).
- **Refinar**: ver qué órganos, años o ponentes hay para un tema y acotar.
- **Descargar en paralelo** las que quieras: **PDF oficial + texto íntegro + ECLI**,
  con extracción rápida (PyMuPDF). Modo "solo texto" si no quieres guardar el PDF.
- **Resolver el captcha** de descarga masiva automáticamente con la visión de Claude.

Es **gratis** y cubre **toda España** (TS, AN, TSJ, Audiencias Provinciales,
Juzgados…). El CENDOJ es público: **no hay login ni usuario**.

## Requisitos

- **Python 3.10+**
- **Claude Desktop** (o cualquier cliente MCP). En Claude Cowork funciona igual.

## Instalación (Windows, recomendada)

```powershell
git clone https://github.com/DerechoVirtual/mcp-cendoj-sentencias.git
cd mcp-cendoj-sentencias

# Crear el entorno e instalar dependencias (con uv; también vale pip + venv)
uv venv
uv pip install -e .
```

> En carpetas sincronizadas (OneDrive/Dropbox) usa `uv pip install --link-mode=copy -e .`

Conéctalo a Claude Desktop con un doble clic en **`instalar_en_claude_desktop.bat`**
(escribe la entrada con la app cerrada y te avisa para abrirla). Si lo prefieres
a mano, edita `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "cendoj-sentencias": {
      "command": "C:\\ruta\\al\\.venv\\Scripts\\python.exe",
      "args": ["C:\\ruta\\al\\server.py"]
    }
  }
}
```

Reinicia Claude Desktop por completo. Aparecerán las herramientas.

## Configuración (`.env`) — **todo opcional**

El servidor **funciona sin configurar nada**: abre y renueva la sesión del CENDOJ
él solo. Si quieres, copia `.env.example` a `.env` y ajusta:

| Variable | Obligatoria | Para qué |
|---|---|---|
| `CENDOJ_COOKIE` | ❌ | Tu cookie `JSESSIONID` del navegador, si prefieres usar tu propia sesión. Vacío = sesión automática. |
| `DOWNLOAD_DIR` | ❌ | Dónde guardar PDFs y textos. Def.: `…\Documents\sentencias-cendoj`. |
| `CENDOJ_BASE` | ❌ | Base del buscador (no tocar salvo cambio de dominio). |

## Cómo se usa (en lenguaje natural)

Habla con Claude con normalidad. Por ejemplo:

> *"Búscame sentencias del Tribunal Supremo sobre deducción del IVA de cuotas
> soportadas y descárgame las 8 primeras con sus fundamentos jurídicos."*

> *"Sentencias de la **Audiencia Provincial de Valladolid** sobre **usufructo**, y
> sácame 5 párrafos clave con su ECLI."* → Claude filtra por provincia + AP + civil,
> elige las más relevantes por el resumen y te da los párrafos citables.

Claude llamará a las herramientas por ti:

1. **`buscar_sentencias`** → lista con ROJ, ECLI, fecha, sala, ponente y resumen.
2. **`descargar_sentencias`** → baja PDF + texto de las que pidas (`"todas"`,
   `"1,3,5"`, `"1-8"` o por ROJ).
3. Si salta el captcha, Claude **lee la imagen y llama a `resolver_captcha`** solo;
   la descarga sigue automáticamente.

### Las herramientas

| Herramienta | Qué hace |
|---|---|
| `buscar_sentencias(consulta, base="TS", maximo=20, fecha_desde, fecha_hasta, tipo_resolucion, jurisdiccion, provincia, tipo_organo)` | Busca y lista resultados con metadatos y resumen. Filtros: jurisdicción (`CIVIL`…), `provincia` (Valladolid…), `tipo_organo` (`AP`, `TSJ`, `JPI`…). No descarga. |
| `buscar_por_cita(cita)` | Localiza una resolución por su **ECLI** (`ECLI:ES:TS:2014:4786`) o **ROJ** (`STS 4786/2014`) exacto. |
| `opciones_busqueda(consulta, campo="organos")` | Facetas para refinar: `organos`, `anos` o `ponentes` disponibles para un tema. |
| `descargar_sentencias(seleccion="todas", incluir_texto=True, max_chars=0, guardar_pdf=True)` | Descarga **en paralelo** PDF + texto de la última búsqueda. `guardar_pdf=False` = solo texto. Devuelve la imagen del captcha si el CENDOJ lo exige. |
| `resolver_captcha(texto)` | Valida el captcha leído de la imagen y continúa la descarga. |
| `estado()` | Diagnóstico: sesión, extractor (PyMuPDF/pypdf), última búsqueda, descarga en curso y carpeta. |

## Consejos de búsqueda (gotchas del CENDOJ)

- **Sensible a tildes y comillas.** Si una consulta da 0 resultados, prueba sin
  tildes o quitando las comillas. Las comillas exigen frase exacta.
- `base="TS"` da el **texto íntegro** del Supremo. `base="AN"` cubre **todas** las
  instancias (TS, AN, TSJ, AP, juzgados).
- El captcha solo aparece en la **descarga**, nunca en la búsqueda, y tras varias
  descargas seguidas. Resolver uno desbloquea un bloque.

## Uso responsable

El captcha existe para frenar la descarga masiva. Esta herramienta está pensada
para uso **individual y moderado** (un profesional o un estudiante consultando
jurisprudencia, resolviendo el captcha puntualmente como haría una persona). Para
descargas de gran volumen, usa la infraestructura adecuada con proxies, no
martillees el CENDOJ desde una sola IP.

## Privacidad y seguridad

- `.env` y `.venv/` están en `.gitignore`. Este repositorio **no contiene
  credenciales ni datos**.
- El servidor solo lee/descarga del CENDOJ y guarda en tu carpeta local. No envía
  nada a terceros.

## Licencia

MIT (ver `LICENSE`).
