# -*- coding: utf-8 -*-
"""Banco de 30 pruebas del motor de doctrina FGE (local, sin red).

Cada caso: consulta + criterio de acierto (ref esperada en el top-3, o palabra
clave en el titulo del top-3). Mide latencia por consulta. Exit 0 si todo pasa.
"""
import io
import re
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import fge_engine as F

# (consulta, kwargs, esperado) — esperado: 'ref:FIS-…' o texto que debe aparecer
# en el bloque de resultados (título/cita), case/tilde-insensible.
CASOS = [
    # --- citas exactas (lookup directo) ---
    ("Circular 1/2023", {}, "ref:FIS-C-2023-00001"),
    ("consulta 2/2026", {}, "ref:FIS-Q-2026-00002"),
    ("Instrucción 1/2020", {}, "ref:FIS-I-2020-00001"),
    ("FIS-C-2011-00006", {}, "ref:FIS-C-2011-00006"),
    ("¿Qué dice la Circular 2/2025 de la audiencia preliminar?", {}, "ref:FIS-C-2025-00002"),
    # --- temas modernos ---
    ("okupacion de viviendas medidas cautelares", {}, "usurpación"),
    ("reforma de los delitos contra la libertad sexual ley solo sí es sí", {}, "libertad sexual"),
    ("delitos de odio", {}, "odio"),
    ("dispensa de la obligación de declarar del artículo 416 LECrim en violencia de género", {}, "violencia"),
    ("responsabilidad penal de las personas jurídicas", {}, "personas jurídicas"),
    ("prision permanente revisable", {}, "permanente"),
    ("menores extranjeros no acompañados determinación de la edad", {}, "menores extranjeros"),
    ("seguridad vial alcoholemia", {}, "seguridad vial"),
    ("trata de seres humanos", {}, "trata"),
    ("siniestralidad laboral", {}, "siniestralidad"),
    ("incendios forestales", {}, "incendios"),
    ("acoso escolar", {}, "acoso escolar"),
    ("expulsión sustitutiva de la pena para extranjeros artículo 89", {}, "expulsión"),
    ("conformidad en el procedimiento abreviado", {}, "conformidad"),
    ("decomiso", {}, "decomiso"),
    ("blanqueo de capitales", {}, "blanqueo"),
    ("agente encubierto informático", {}, "agente encubierto"),
    ("dispositivos telemáticos de control en violencia de género", {}, "dispositivos telemáticos"),
    ("tribunal del jurado ámbito de aplicación", {}, "jurado"),
    ("responsabilidad penal de los menores ley 5/2000", {}, "menores"),
    # --- históricas ---
    ("cheque en descubierto", {}, "cheque"),
    ("insumisión prestación social sustitutoria", {}, "insumisión"),
    # --- filtros ---
    ("", {"tipo": "circular", "desde": 2023}, "Circular"),
    ("violencia de género", {"tipo": "circular"}, "violencia"),
    ("", {"materia": "Extranjería", "limite": 5}, "Extranjería"),
]


def norm(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def main():
    F._cargar()
    fallos, tiempos = [], []
    for i, (q, kw, esp) in enumerate(CASOS, 1):
        t0 = time.perf_counter()
        out = F.buscar(q, **kw)
        dt = (time.perf_counter() - t0) * 1000
        tiempos.append(dt)
        top = "\n".join(out.splitlines()[:16])  # cabecera + top-3 aprox
        if esp.startswith("ref:"):
            ok = esp[4:] in top
        else:
            ok = norm(esp) in norm(top)
        estado = "OK " if ok else "FALLO"
        print(f"[{i:02d}] {estado} {dt:6.1f} ms  «{q[:60]}»{' '+str(kw) if kw else ''}")
        if not ok:
            fallos.append((i, q, kw, esp, out[:700]))
    print(f"\n== {len(CASOS)-len(fallos)}/{len(CASOS)} OK · latencia media "
          f"{sum(tiempos)/len(tiempos):.1f} ms · p95 "
          f"{sorted(tiempos)[int(len(tiempos)*0.95)-1]:.1f} ms")
    for i, q, kw, esp, out in fallos:
        print(f"\n--- FALLO [{i}] «{q}» esperaba {esp!r}:\n{out}")
    sys.exit(1 if fallos else 0)


if __name__ == "__main__":
    main()
