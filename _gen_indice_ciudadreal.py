# -*- coding: utf-8 -*-
"""Índice EMPAQUETADO del BOP de Ciudad Real (SIGEM de la Diputación; patrón Madrid/Cádiz).

Por qué: el buscador del BOP (/buscador) solo indexa el TÍTULO del anuncio y el título
nunca dice el municipio ("Tomelloso ordenanza" = 0 resultados), y su filtro `entidad`
está roto. Lo que SÍ resuelve doc_id -> municipio EXACTO es el SUMARIO DIARIO
/bop/AAAA/MM/DD, agrupado por entidad (<h3 class=admons>AYUNTAMIENTOS</h3> ->
<p class=clasificaciones>MUNICIPIO</p> -> getDocument.do?doc=N). Se recorren TODOS los
días desde 2013 (antes el boletín solo existe como PDF completo) y se guardan solo los
anuncios NORMATIVOS de ayuntamientos. El parser, el resolutor de entidades (variantes
sucias, patronatos, EATIM) y el filtro viven en el backend (bop_ciudadreal).

Reanudable: el volcado CRUDO por día (todos los anuncios de AYUNTAMIENTOS) se guarda en
%TEMP%/bop-crawl/ciudadreal_sumarios.json cada 200 días; al relanzar se saltan los días
ya hechos y el índice se reempaqueta entero (se puede afinar el filtro sin recrawlear).

Salida: ordenanzas_data/ciudadreal_indice.json  {"meta": {...}, "anuncios": [...]}
        anuncio = {"o": municipio (clave del mapa), "t": título, "n": nº anuncio,
                   "d": doc id del PDF, "f": aaaammdd, "b": nº boletín[, "e": patronato…]}
Uso:    python -X utf8 _gen_indice_ciudadreal.py [--desde 2013-01-01] [--hasta hoy]
                                                 [--workers 8] [--solo-empaquetar]
"""
import concurrent.futures as cf
import datetime as dt
import json
import os
import sys
import tempfile
import time

import bop_ciudadreal as CR

HERE = os.path.dirname(os.path.abspath(__file__))
SALIDA = CR._IDX_FP
ESTADO = os.path.join(tempfile.gettempdir(), "bop-crawl", "ciudadreal_sumarios.json")


def dia(iso):
    """-> (iso, {"n": nº boletín, "a": anuncios} | None si no hubo boletín, error)."""
    url = f"{CR.BASE}/bop/{iso[:4]}/{iso[5:7]}/{iso[8:10]}"
    ult = ""
    for intento in range(4):
        try:
            html = CR._get(url, timeout=25).decode("iso-8859-1", "replace")
            if 'id="boletin"' not in html and "bop_centro" not in html:
                raise ValueError("página inesperada")
            num, anuncios = CR.parse_sumario(html)
            if not anuncios and 'class="admons"' not in html:
                return iso, None, ""              # día sin boletín (o solo PDF completo)
            return iso, {"n": num, "a": anuncios}, ""
        except Exception as e:  # noqa: BLE001
            ult = f"{type(e).__name__}: {str(e)[:60]}"
            time.sleep(1.5 * (intento + 1))
    return iso, None, ult or "error"


def cargar_estado():
    try:
        return json.load(open(ESTADO, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"dias": {}}


def guardar_estado(est):
    os.makedirs(os.path.dirname(ESTADO), exist_ok=True)
    tmp = ESTADO + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(est, f, ensure_ascii=False)
    os.replace(tmp, ESTADO)


def empaquetar(est, desde, hasta):
    anuncios, sin_resolver, patronatos = [], {}, 0
    dias = sorted(d for d in est["dias"] if desde <= d <= hasta)
    for iso in dias:
        v = est["dias"][iso]
        if not v:
            continue
        for a in v["a"]:
            if not CR.es_normativo(a["t"]):
                continue
            k, sub = CR.resolver_entidad(a["ent"])
            if not k:
                sin_resolver[a["ent"]] = sin_resolver.get(a["ent"], 0) + 1
                continue
            rec = {"o": k, "t": a["t"][:220], "n": a["n"], "d": a["d"], "f": iso.replace("-", ""),
                   "b": v.get("n") or 0}
            if sub:
                rec["e"] = sub[:80]
                patronatos += 1
            anuncios.append(rec)
    meta = {"generado": dt.date.today().isoformat(), "desde": desde, "hasta": hasta,
            "dias_con_boletin": sum(1 for d in dias if est["dias"][d]), "fuente": CR.BASE,
            "municipios": len({a["o"] for a in anuncios})}
    with open(SALIDA, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "anuncios": anuncios}, f, ensure_ascii=False)
    print(f"EMPAQUETADO: {len(anuncios)} anuncios normativos de {meta['municipios']} municipios "
          f"({patronatos} de patronatos/institutos colgados de su municipio), "
          f"{os.path.getsize(SALIDA)/1e6:.2f} MB -> {SALIDA}")
    if sin_resolver:
        print("Entidades SIN resolver (descartadas):")
        for e, c in sorted(sin_resolver.items(), key=lambda x: -x[1])[:40]:
            print(f"   {c:4d}  {e}")


def main():
    args = sys.argv[1:]
    desde = args[args.index("--desde") + 1] if "--desde" in args else "2013-01-01"
    hasta = args[args.index("--hasta") + 1] if "--hasta" in args else dt.date.today().isoformat()
    workers = int(args[args.index("--workers") + 1]) if "--workers" in args else 8
    est = cargar_estado()
    if "--solo-empaquetar" not in args:
        d0, d1 = dt.date.fromisoformat(desde), dt.date.fromisoformat(hasta)
        todos = [(d0 + dt.timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]
        # hoy y ayer se recrawlean siempre (el boletín del día puede llegar tarde)
        recientes = {(dt.date.today() - dt.timedelta(days=i)).isoformat() for i in range(2)}
        pend = [d for d in todos if d not in est["dias"] or d in recientes]
        print(f"{len(todos)} días entre {desde} y {hasta}; {len(pend)} pendientes; {workers} hilos", flush=True)
        t0 = time.time()
        con = errores = 0
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            for i, (iso, v, err) in enumerate(ex.map(dia, pend), 1):
                if err:
                    errores += 1
                    print("ERR", iso, err, flush=True)
                else:
                    est["dias"][iso] = v
                    con += bool(v)
                if i % 200 == 0:
                    guardar_estado(est)
                    print(f"{i}/{len(pend)} días · {con} con boletín · {errores} err · "
                          f"{time.time()-t0:.0f}s", flush=True)
        guardar_estado(est)
        print(f"CRAWL: {len(pend)} días en {time.time()-t0:.0f}s · {con} con boletín · {errores} errores "
              f"(los días con error quedan pendientes para la próxima ejecución)", flush=True)
    empaquetar(est, desde, hasta)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
