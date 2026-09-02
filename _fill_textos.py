# -*- coding: utf-8 -*-
"""Empaqueta el TEXTO de las normas de un catálogo de ciudad (AdaptadorWeb) en
ordenanzas_data/<ciudad>_textos/<id>.txt, patrón León: 0 red y 0 OCR en runtime.

Motivo (2-sep-2026): sevilla.org devuelve 503 / corta la conexión de forma
intermitente y el conector se quedaba sin párrafo literal ("no se pudo descargar
el texto de la fuente oficial"). Con el texto empaquetado la lectura es local y
la web municipal queda solo como enlace oficial (y como respaldo).

Uso:  ./.venv/Scripts/python.exe _fill_textos.py sevilla [--workers 3] [--max-ocr 40] [--solo-faltan]
"""
import concurrent.futures as cf
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.request

_ENV = os.path.join(os.path.expanduser("~"), ".claude", ".env")
try:
    for ln in open(_ENV, encoding="utf-8"):
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1)
            os.environ[k.strip()] = v.strip().strip('"').strip("'")
except Exception:  # noqa: BLE001
    pass

import ordenanzas_engine as OE  # noqa: E402
import bop_engine as B  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}


_BAJADOS = {}   # url -> (bytes, via): Murcia enlaza el MISMO zip de 11 MB en 40 normas


def bajar(url, intentos=6, timeout=40):
    """Descarga con reintentos (la web municipal puede ir a rachas). (bytes, via)."""
    if url in _BAJADOS:
        return _BAJADOS[url]
    r = _bajar(url, intentos, timeout)
    if len(_BAJADOS) < 40:
        _BAJADOS[url] = r
    return r


def _bajar(url, intentos=6, timeout=40):
    ultimo = ""
    for i in range(intentos):
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout)
            b = r.read()
            if r.status == 200 and len(b) > 500:
                return b, "live"
            ultimo = f"HTTP {r.status} {len(b)}b"
        except Exception as e:  # noqa: BLE001
            ultimo = str(e)[:80]
        time.sleep(2 + 2 * i)
    # respaldo: Wayback Machine (última copia, fichero crudo con 'id_')
    for wb in (f"http://web.archive.org/web/2id_/{url}", f"http://web.archive.org/web/2024id_/{url}"):
        try:
            r = urllib.request.urlopen(urllib.request.Request(wb, headers=UA), timeout=60)
            b = r.read()
            if r.status == 200 and len(b) > 500:
                return b, "wayback"
        except Exception as e:  # noqa: BLE001
            ultimo = "wayback: " + str(e)[:80]
    raise RuntimeError(ultimo)


def texto_de(ad, norma, datos, max_ocr):
    if norma.get("formato") == "zip" and norma.get("miembro"):
        import io, zipfile
        with zipfile.ZipFile(io.BytesIO(datos)) as z:
            datos = z.read(norma["miembro"])
    if norma.get("formato") in ("pdf", "zip") or datos[:5] == b"%PDF-":
        t = OE._pdf_a_texto(datos)
        via = "directo"
        # capa de texto inservible (CID sin ToUnicode / escaneado) -> OCR una sola vez
        if len(B._PALS.findall(t)) < 15 or len(t) < 400:
            t2, via2 = B._pdf_bytes_texto(datos, ocr=True, max_pag=max_ocr)
            if len(t2) > len(t):
                t, via = OE._reparar_parrafos_pdf(t2), via2
        return AdaptadorRecorte(ad, norma, t), via
    return ad._texto_de(norma, norma["url"]) if False else _html(ad, datos), "html"


def AdaptadorRecorte(ad, norma, t):
    return ad._recortar_por_titulo(t, norma["titulo"])


def _html(ad, datos):
    enc = "utf-8"
    m = re.search(rb'charset=["\']?([A-Za-z0-9_-]+)', datos[:2000])
    if m:
        enc = m.group(1).decode("ascii", "replace")
    htm = datos.decode(enc, "replace")
    rec = ad.catalogo()["meta"].get("recorte")
    if rec:
        m2 = re.search(rec, htm, re.S)
        if m2:
            htm = m2.group(1) if m2.groups() else m2.group(0)
    return OE._html_a_texto(htm)


def main():
    args = sys.argv[1:]
    ciudad = args[0]
    workers = int(args[args.index("--workers") + 1]) if "--workers" in args else 3
    max_ocr = int(args[args.index("--max-ocr") + 1]) if "--max-ocr" in args else 40
    solo_faltan = "--solo-faltan" in args
    gz = "--gz" in args          # .txt.gz: ~4x menos en el repo (el motor lee ambos)
    ad = OE.ADAPTADORES[ciudad]
    fp_cat = os.path.join(OE.DATA_DIR, ciudad + ".json")
    cat = json.load(open(fp_cat, encoding="utf-8"))
    outdir = os.path.join(OE.DATA_DIR, ciudad + "_textos")
    os.makedirs(outdir, exist_ok=True)
    resumen = {"ok": 0, "fail": 0, "skip": 0, "via": {}}

    def uno(n):
        fp = os.path.join(outdir, n["id"] + (".txt.gz" if gz else ".txt"))
        if solo_faltan and os.path.exists(fp) and os.path.getsize(fp) > 300:
            return n["id"], "skip", 0, ""
        urls = [n["url"]] + [u for u in n.get("urls", []) if u != n["url"]]
        mejor, via_b, via_t, err = "", "", "", ""
        for u in urls[:3]:
            try:
                datos, via_b = bajar(u)
                t, via_t = texto_de(ad, n, datos, max_ocr)
            except Exception as e:  # noqa: BLE001
                err = str(e)[:100]
                continue
            if len(t) > len(mejor):
                mejor = t
            if len(mejor) > 5000:
                break
        if len(mejor) < 300:
            return n["id"], "fail", len(mejor), err or "texto vacío"
        if gz:
            import gzip
            with gzip.open(fp, "wt", encoding="utf-8") as f:
                f.write(mejor)
        else:
            with open(fp, "w", encoding="utf-8") as f:
                f.write(mejor)
        return n["id"], f"{via_b}/{via_t}", len(mejor), ""

    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for nid, via, n, err in ex.map(uno, cat["normas"]):
            if via == "fail":
                resumen["fail"] += 1
                print(f"[FAIL] {nid:55s} {err}", flush=True)
            elif via == "skip":
                resumen["skip"] += 1
            else:
                resumen["ok"] += 1
                resumen["via"][via] = resumen["via"].get(via, 0) + 1
                print(f"[ok  ] {nid:55s} {n:7d} chars  {via}", flush=True)
    # actualizar catálogo: texto por norma + meta
    for n in cat["normas"]:
        for ext in (".txt.gz", ".txt"):
            fp = os.path.join(outdir, n["id"] + ext)
            if os.path.exists(fp) and os.path.getsize(fp) > 300:
                n["texto"] = n["id"] + ext
                break
    cat["meta"]["textos_dir"] = ciudad + "_textos"
    cat["meta"]["textos_fecha"] = dt.date.today().isoformat()
    with open(fp_cat, "w", encoding="utf-8") as f:
        json.dump(cat, f, ensure_ascii=False, indent=1)
    tam = sum(os.path.getsize(os.path.join(outdir, x)) for x in os.listdir(outdir))
    print("=" * 70)
    print(f"{ciudad}: {resumen} · {tam/1e6:.1f} MB · {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
