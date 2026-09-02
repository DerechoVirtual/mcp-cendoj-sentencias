# -*- coding: utf-8 -*-
"""Crea un deployment de PREVIEW del conector en Vercel DESDE GITHUB (gitSource),
que es la única vía que cumple el guardarraíl (nunca desde copia local), y espera
a que esté READY. Imprime el hostname del preview.

Uso: python -X utf8 _deploy_preview.py <rama> [--prod]
     --prod: target production (SOLO con el sí expreso de Carlos; el flujo normal
             es push a main, que Vercel construye sola).
"""
import json
import os
import sys
import time
import urllib.request

ENV = "C:/Users/carlo/OneDrive/Documentos/antigravity/mcp-cendoj/.env"
TEAM = "team_AuHS9PAw6wmKxMihW0VmRl7s"
PROJECT = "prj_wWpgFeSU2yHS2BaOKj391TvjjMwi"
REPO_ID = 1267286079


def token():
    for ln in open(ENV, encoding="utf-8"):
        if ln.startswith("VERCEL_TOKEN="):
            return ln.split("=", 1)[1].strip().strip('"')
    raise SystemExit("sin VERCEL_TOKEN")


def api(method, path, body=None):
    req = urllib.request.Request("https://api.vercel.com" + path, method=method,
                                 data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": f"Bearer {token()}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def main():
    rama = sys.argv[1]
    prod = "--prod" in sys.argv
    if prod and os.environ.get("JPD_DEPLOY_OK") != "1":
        raise SystemExit("producción exige JPD_DEPLOY_OK=1 (sí expreso de Carlos)")
    body = {"name": "jurisprudenciator-mcp", "project": PROJECT,
            "gitSource": {"type": "github", "repoId": REPO_ID, "ref": rama}}
    if prod:
        body["target"] = "production"      # sin `target` = preview (la API rechaza "preview")
    d = api("POST", f"/v13/deployments?teamId={TEAM}&forceNew=1", body)
    dpl, url = d["id"], d.get("url")
    print("deployment", dpl, url, flush=True)
    t0 = time.time()
    while time.time() - t0 < 900:
        s = api("GET", f"/v13/deployments/{dpl}?teamId={TEAM}")
        st = s.get("readyState") or s.get("state")
        print(f"  {st} ({time.time()-t0:.0f}s)", flush=True)
        if st in ("READY", "ERROR", "CANCELED"):
            if st != "READY":
                raise SystemExit(f"deployment {st}")
            print("HOST", s.get("url"))
            return
        time.sleep(10)
    raise SystemExit("timeout esperando el deployment")


if __name__ == "__main__":
    main()
