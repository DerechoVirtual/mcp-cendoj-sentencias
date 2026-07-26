# -*- coding: utf-8 -*-
"""Banco de pruebas + medicion de tiempos del backend 'tenerife'."""
import time, sys, statistics
import _probe_tenerife5 as T

CASOS = [
    ("Tacoronte", "ordenanza"),
    ("Santa Cruz de Tenerife", "terrazas"),
    ("San Cristóbal de La Laguna", "ordenanza"),
    ("Arona", "residuos"),
    ("Adeje", "taxi"),
    ("Granadilla de Abona", "ordenanza"),
    ("La Orotava", "ordenanza fiscal"),
    ("Los Realejos", "ordenanza"),
    ("Puerto de la Cruz", "ordenanza"),
    ("Candelaria", "ordenanza"),
    ("Santa Cruz de la Palma", "ordenanza"),
    ("Los Llanos de Aridane", "ordenanza"),
    ("San Sebastián de la Gomera", "ordenanza"),
    ("Valverde", "ordenanza"),
    ("Frontera", "ordenanza"),
    ("Villa de Mazo", "ordenanza"),
    ("El Pinar de El Hierro", "ordenanza"),
    ("Vilaflor de Chasna", "ordenanza"),
    ("Güímar", "ordenanza"),
    ("Icod de los Vinos", "ordenanza"),
]

if __name__ == "__main__":
    reps = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    tb, tl, okb, okl, fallos = [], [], 0, 0, []
    for muni, mat in CASOS:
        for r in range(reps):
            T._CACHE.clear()          # medir siempre en frio
            t0 = time.time()
            try:
                res = T.buscar(muni, mat)
            except Exception as e:  # noqa: BLE001
                fallos.append((muni, mat, "buscar", repr(e))); continue
            dtb = time.time() - t0
            tb.append(dtb)
            if res:
                okb += 1
            else:
                fallos.append((muni, mat, "buscar", "0 resultados"))
                if r == 0:
                    print(f"{muni:28} {mat:16} SIN RESULTADOS  ({dtb:.2f}s)")
                continue
            t1 = time.time()
            try:
                txt, sc, np = T.leer(res[0])
            except Exception as e:  # noqa: BLE001
                fallos.append((muni, mat, "leer", repr(e))); continue
            dtl = time.time() - t1
            tl.append(dtl)
            if txt and len(txt) > 400:
                okl += 1
            else:
                fallos.append((muni, mat, "leer", f"score={sc:.2f} chars={len(txt or '')}"))
            if r == 0:
                print(f"{muni:28} {mat:16} n={len(res):3} busq={dtb:.2f}s lec={dtl:.2f}s "
                      f"pdf={np}p score={sc:.2f} chars={len(txt or '')}  {res[0]['fecha']}")
    print()
    print(f"BUSQUEDA  n={len(tb)} media={statistics.mean(tb):.2f}s mediana={statistics.median(tb):.2f}s "
          f"max={max(tb):.2f}s  ok={okb}/{len(CASOS)*reps}")
    if tl:
        print(f"LECTURA   n={len(tl)} media={statistics.mean(tl):.2f}s mediana={statistics.median(tl):.2f}s "
              f"max={max(tl):.2f}s  ok={okl}/{len(tl)}")
    if fallos:
        print("\nFALLOS:")
        for f in fallos:
            print("  ", f)
