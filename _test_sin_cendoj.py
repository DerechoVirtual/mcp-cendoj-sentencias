# -*- coding: utf-8 -*-
"""Banco: la fuente NUNCA se nombra en lo que VE el abogado.

Regla de Carlos (01-sep-2026): "CENDOJ no aparece en ningun lado visible de la
aplicacion". Lo visible del conector es lo que viaja por el protocolo MCP:
las INSTRUCTIONS del servidor y el NOMBRE + DESCRIPCION de cada tool (que el
modelo lee y repite tal cual al usuario cuando le preguntan de donde saca la
jurisprudencia). Los comentarios y las variables de entorno del codigo NO son
visibles y se dejan como estan.

Se analiza el fuente con AST (sin importar el modulo ni tocar la red), asi que
corre en cualquier maquina y en CI. Uso: python _test_sin_cendoj.py
"""
import ast
import sys

PROHIBIDAS = ("cendoj", "poderjudicial", "centro de documentacion judicial",
              "centro de documentación judicial", "consejo general del poder judicial")
FUENTES = ("server_http.py", "server.py")


def _es_tool(fn):
    return any("tool" in ast.dump(d).lower() for d in fn.decorator_list)


def _malas(texto):
    bajo = (texto or "").lower()
    return [p for p in PROHIBIDAS if p in bajo]


def revisar(ruta):
    fallos = []
    arbol = ast.parse(open(ruta, encoding="utf-8").read())
    for nodo in ast.walk(arbol):
        # 1) Descripcion de cada tool = su docstring.
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)) and _es_tool(nodo):
            for p in _malas(ast.get_docstring(nodo)):
                fallos.append(f"{ruta}:{nodo.lineno} tool {nodo.name}() nombra '{p}'")
        # 2) instructions del servidor (lo primero que lee el modelo).
        if isinstance(nodo, ast.Assign):
            for destino in nodo.targets:
                if isinstance(destino, ast.Name) and destino.id == "_INSTRUCTIONS":
                    try:
                        valor = ast.literal_eval(nodo.value)
                    except Exception:  # noqa: BLE001  (concatenacion no literal)
                        valor = ast.get_source_segment(
                            open(ruta, encoding="utf-8").read(), nodo) or ""
                    for p in _malas(valor):
                        fallos.append(f"{ruta}:{nodo.lineno} _INSTRUCTIONS nombra '{p}'")
    return fallos


def main():
    fallos = []
    for f in FUENTES:
        try:
            fallos += revisar(f)
        except FileNotFoundError:
            continue
    if fallos:
        print("FALLA: la fuente se nombra en texto que ve el abogado")
        for f in fallos:
            print("  -", f)
        return 1
    print("OK: ni instructions ni descripciones de tools nombran la fuente")
    return 0


if __name__ == "__main__":
    sys.exit(main())
