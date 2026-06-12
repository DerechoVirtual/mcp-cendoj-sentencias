"""
Instalador del servidor MCP 'cendoj-sentencias' en Claude Desktop (Windows).

Por que existe: Claude Desktop reescribe su archivo de configuracion cuando
guarda preferencias, y si la entrada se anade con la app ABIERTA, la app la
borra (solo conserva lo que tenia en memoria al arrancar). La unica forma
fiable es escribir la entrada con la app CERRADA y luego abrirla.

Este script:
  1) Espera a que Claude Desktop este completamente cerrado.
  2) Anade/actualiza la entrada en claude_desktop_config.json (preservando todo
     lo demas: otros conectores, preferencias, etc.).
  3) Te avisa para que abras Claude Desktop.

Ejecutar: doble clic en 'instalar_en_claude_desktop.bat' (o con cualquier python).
"""

import json
import os
import subprocess
import sys
import time

PROYECTO = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(os.environ["APPDATA"], "Claude", "claude_desktop_config.json")
PYTHON_VENV = os.path.join(PROYECTO, ".venv", "Scripts", "python.exe")
SERVER = os.path.join(PROYECTO, "server.py")
NOMBRE = "cendoj-sentencias"


def claude_abierto() -> bool:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq Claude.exe", "/NH"],
            capture_output=True, text=True,
        ).stdout
        return "Claude.exe" in out
    except Exception:
        return False


def main() -> int:
    print("== Instalador MCP 'cendoj-sentencias' para Claude Desktop ==\n")

    if not os.path.exists(PYTHON_VENV) or not os.path.exists(SERVER):
        print("ERROR: no encuentro el servidor o el entorno virtual en:")
        print("  ", PROYECTO)
        print("Crea antes el entorno:  uv venv  &&  uv pip install -e .")
        return 1

    # 1) Esperar a que Claude Desktop este cerrado
    aviso_dado = False
    while claude_abierto():
        if not aviso_dado:
            print(">> Claude Desktop esta ABIERTO.")
            print(">> Cierralo POR COMPLETO: icono de la bandeja del sistema")
            print("   (abajo derecha, junto al reloj) -> clic derecho -> Salir.\n")
            aviso_dado = True
        print("   ...esperando a que se cierre", end="\r")
        time.sleep(2)

    print("\nClaude Desktop cerrado. Escribiendo configuracion...        ")

    # 2) Cargar config existente (preservando todo)
    if os.path.exists(CONFIG):
        with open(CONFIG, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    else:
        os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
        cfg = {}

    cfg.setdefault("mcpServers", {})
    cfg["mcpServers"][NOMBRE] = {
        "command": PYTHON_VENV,
        "args": [SERVER],
    }

    # 3) Guardar en UTF-8 SIN BOM (JSON.parse de la app falla con BOM)
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    servidores = ", ".join(cfg["mcpServers"].keys())
    print(f"\nOK. Servidores MCP en la config: {servidores}")
    print("\n>> Ya puedes ABRIR Claude Desktop.")
    print(">> En 'Conectores' (seccion Escritorio) aparecera 'cendoj-sentencias'")
    print("   con las herramientas: buscar_sentencias, buscar_por_cita,")
    print("   opciones_busqueda, leer_sentencias, resolver_captcha y estado.\n")
    return 0


if __name__ == "__main__":
    rc = main()
    try:
        input("Pulsa ENTER para cerrar esta ventana...")
    except EOFError:
        pass
    sys.exit(rc)
