# CENDOJ Sentencias — Servidor MCP

Busca y **lee sentencias del CENDOJ** (el buscador oficial y **gratuito** de
jurisprudencia del Poder Judicial, `poderjudicial.es`) directamente desde
**Claude** (Desktop / Cowork). Encuentra la **mejor** sentencia para un caso,
extrae los **párrafos exactos** con su **ECLI**, y trabaja a **gran velocidad**.

> **⚡ Rápido de verdad:** la mejor sentencia con sus párrafos en **~2-3 s**, y
> **30 sentencias con sus párrafos clave en ~6 s**. El control "Control Descargas
> masivas" del CENDOJ es **por sesión** (no por IP): el servidor reparte las
> descargas entre varias sesiones frescas (**multi-sesión**) y **esquiva los
> captchas sin pausas ni intervención del usuario**. Sin API keys, sin coste.

## Qué puede hacer

- **Buscar** jurisprudencia con todos los filtros del CENDOJ: base TS/AN, fechas,
  tipo de resolución, **jurisdicción** (CIVIL, PENAL…), **provincia/sede**
  (Valladolid, Alicante…) y **tipo de órgano** (AP, TSJ, Juzgado de 1ª Instancia,
  Mercantil…). Pagina automáticamente si pides más de 50.
- Ver **ROJ, ECLI, fecha, sala, ponente y un RESUMEN(auto)** de cada sentencia
  (extracto del propio texto) — para **elegir la mejor por el resumen, sin
  descargar nada**.
- **Localizar por cita**: por **ECLI o ROJ exacto** (verificar una cita en segundos).
- **Leer** el texto íntegro **o solo los párrafos exactos** que tocan el tema, de
  una o de decenas de sentencias a la vez. **Por defecto no guarda nada** en tu disco.
- **Refinar** por órganos, años o ponentes disponibles para un tema.

Es **gratis** y cubre **toda España** (TS, AN, TSJ, Audiencias Provinciales,
Juzgados…). El CENDOJ es público: **no hay login ni usuario**.

## Requisitos

- **Python 3.10+** (con **PyMuPDF** para extracción de texto rápida; cae a `pypdf`).
- **Claude Desktop** (o cualquier cliente MCP). En Claude Cowork funciona igual.

## Instalación (Windows)

```powershell
git clone https://github.com/DerechoVirtual/mcp-cendoj-sentencias.git
cd mcp-cendoj-sentencias

uv venv
uv pip install -e .          # en OneDrive/Dropbox: añade --link-mode=copy
```

Conéctalo con doble clic en **`instalar_en_claude_desktop.bat`** (escribe la
entrada con la app cerrada y te avisa), o a mano en
`%APPDATA%\Claude\claude_desktop_config.json`:

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

Reinicia Claude Desktop por completo.

## Configuración (`.env`) — **todo opcional**

Funciona **sin configurar nada** (abre y renueva la sesión él solo):

| Variable | Para qué |
|---|---|
| `CENDOJ_COOKIE` | Tu `JSESSIONID` del navegador, si prefieres tu sesión. Vacío = automática. |
| `DOWNLOAD_DIR` | Dónde guardar PDFs/textos **si pides guardarlos**. Def.: `…\Documents\sentencias-cendoj`. |
| `CENDOJ_BASE` | Base del buscador (no tocar salvo cambio de dominio). |

## Cómo se usa (en lenguaje natural)

> *"Sentencias de la **AP de Valladolid** sobre **usufructo**: dame los 5 párrafos
> clave de cada una con su ECLI."*

> *"Búscame 30 sentencias sobre **pensión compensatoria con ingresos similares** y
> sácame de cada una el párrafo donde se razona el desequilibrio."*

Claude llamará a las herramientas por ti:

1. **`buscar_sentencias`** → lista con ROJ, ECLI, fecha, ponente y **RESUMEN(auto)**,
   para elegir las mejores **sin descargar nada**.
2. **`leer_sentencias(parrafos=N)`** → lee en memoria y devuelve solo los **N
   párrafos exactos** que tocan el tema (o el texto íntegro con `parrafos=0`).
   **No guarda nada** salvo que lo pidas. Esquiva los captchas solo.

### Las herramientas

| Herramienta | Qué hace |
|---|---|
| `buscar_sentencias(consulta, base="TS", maximo=20, fecha_desde, fecha_hasta, tipo_resolucion, jurisdiccion, provincia, tipo_organo)` | Busca y lista con metadatos y RESUMEN(auto). Filtros de jurisdicción, `provincia`, `tipo_organo`. Pagina si `maximo>50`. |
| `buscar_por_cita(cita)` | Localiza por **ECLI** o **ROJ** exacto. |
| `opciones_busqueda(consulta, campo="organos")` | Facetas para refinar: `organos`, `anos`, `ponentes`. |
| `leer_sentencias(seleccion="todas", parrafos=0, terminos="", max_chars=0, guardar_pdf=False)` | **Lee** el texto (multi-sesión, rapidísimo). `parrafos=N` → solo los **N pasajes exactos** sobre el tema. Por defecto **no guarda** el PDF. |
| `continuar_lectura(texto)` | Fallback histórico — normalmente innecesario (las comprobaciones de seguridad se esquivan solas). |
| `estado()` | Diagnóstico: extractor, sesión, última búsqueda, carpeta. |

## Por qué es tan rápido

- **Multi-sesión**: el captcha del CENDOJ es **por sesión** y salta sobre la 6ª-7ª
  descarga. El motor usa **varias sesiones frescas en paralelo** (5 descargas cada
  una) → casi nunca aparece, y si aparece se **esquiva** abriendo otra sesión. Sin
  pausas, sin resolver captchas.
- **PyMuPDF** para extraer el texto ~10× más rápido que `pypdf`.
- **Modo párrafos**: en vez de volcar el texto íntegro de 30 sentencias (~800.000
  caracteres), devuelve solo los pasajes que tocan el tema (~100.000) → rápido y
  sin saturar.

## Uso responsable

Pensado para uso **profesional e individual** (un abogado/estudiante consultando
jurisprudencia). El multi-sesión reparte la carga, pero no martillees el CENDOJ con
descargas masivas innecesarias: con el RESUMEN(auto) eliges por resumen y solo lees
las que de verdad sirven.

## Privacidad y seguridad

- `.env` y `.venv/` están en `.gitignore`. Este repositorio **no contiene
  credenciales ni datos**.
- Por defecto **no guarda nada** en tu disco (lee en memoria). Solo lee/consulta el
  CENDOJ; no envía nada a terceros.

## Licencia

MIT (ver `LICENSE`).
