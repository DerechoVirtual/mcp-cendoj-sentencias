# -*- coding: utf-8 -*-
"""
Motor Registro Mercantil (BORME por EMPRESA) vía el índice abierto openmercantil.es.

La API de datos abiertos del BOE solo permite el BORME por FECHA exacta (y los
títulos del sumario son provincias, no empresas). Para buscar una sociedad por
NOMBRE o CIF a lo largo del tiempo se usa openmercantil.es (JSON, sin clave, sin
captcha), que reindexa el BORME oficial.

Alcance REAL (honesto):
  ✔ existencia, CIF, estado, tipo y provincia de la sociedad.
  ✔ historial de ACTOS inscritos (constitución, nombramientos/ceses de
    administradores y apoderados, ampliaciones de capital, cambios de domicilio,
    disolución…) con fecha y referencia BORME.
  ✔ administradores/apoderados vigentes e históricos.
  ✘ NO el depósito de cuentas anuales (fecha fiable) ni su contenido financiero
    (eso es de pago en el Registro Mercantil).
  ✘ Sin valor de fe pública (dato reempaquetado por un tercero): para prueba,
    nota/certificación oficial del Registro Mercantil.
"""
import re
import json
import unicodedata
import urllib.parse
import urllib.request
import urllib.error

BASE = "https://openmercantil.es/api/v1"


def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _get_json(path: str, timeout=10):
    url = f"{BASE}{path}"
    req = urllib.request.Request(url, headers={
        "Accept": "application/json", "User-Agent": "jurisprudenciator-mercantil/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status != 200:
                return None
            return json.loads(r.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, ValueError, TimeoutError):
        return None
    except Exception:  # noqa: BLE001
        return None


def _es_cif(q: str) -> bool:
    q = (q or "").strip().upper().replace("-", "").replace(" ", "")
    return bool(re.fullmatch(r"[A-Z]\d{7}[A-Z0-9]", q))


def _cif_norm(q: str) -> str:
    return (q or "").strip().upper().replace("-", "").replace(" ", "")


_FUENTE = ("Fuente: índice del BORME (dato público reempaquetado, sin valor de fe "
           "pública). Para prueba, pide nota oficial del Registro Mercantil.")


def buscar_empresa(consulta: str, maximo_actos: int = 12) -> str:
    """Busca una sociedad por nombre o CIF en el índice del BORME y devuelve su
    ficha: datos registrales + administradores + últimos actos inscritos."""
    consulta = (consulta or "").strip()
    if len(consulta) < 2:
        return "Indica el nombre o el CIF de la empresa."
    d = _get_json(f"/search?q={urllib.parse.quote(consulta)}&limit=15&include_persons=0")
    items = (d or {}).get("items") or []
    if not items:
        return (f"No encuentro ninguna sociedad para «{consulta}» en el índice del "
                "BORME. Puede ser muy reciente/sin actos publicados, operar con otra "
                "razón social, o no ser una sociedad mercantil. " + _FUENTE)

    nq = _norm(consulta)
    best = None
    if _es_cif(consulta):
        cif = _cif_norm(consulta)
        best = next((it for it in items if _cif_norm(it.get("cif", "")) == cif), None)
    if not best:
        best = next((it for it in items if _norm(it.get("name", "")) == nq
                     or nq in [_norm(a) for a in it.get("aliases", [])]), None)
    if not best:
        conword = [it for it in items
                   if all(w in _norm(it.get("name", "")) for w in nq.split())]
        # una sola candidata razonable -> úsala; varias -> pide que afine
        if len(conword) == 1:
            best = conword[0]
        elif conword:
            lista = "\n".join(
                f"  · {it['name']}" + (f" · CIF {it['cif']}" if it.get("cif") else "")
                + f" · {it.get('acts_count', 0)} actos" for it in conword[:8])
            return (f"Varias sociedades coinciden con «{consulta}». Afina con el nombre "
                    f"exacto o el CIF:\n{lista}\n" + _FUENTE)
    if not best:
        lista = "\n".join(
            f"  · {it['name']}" + (f" · CIF {it['cif']}" if it.get("cif") else "")
            for it in items[:8])
        return (f"No hay una coincidencia clara para «{consulta}». Candidatas próximas:\n"
                f"{lista}\nProbable que la sociedad exacta no esté indexada. " + _FUENTE)

    prof = _get_json(f"/company/{best['slug']}")
    if not prof or not prof.get("company"):
        return (f"Localicé «{best.get('name')}» pero no pude cargar su ficha ahora mismo. "
                "Reinténtalo en unos segundos. " + _FUENTE)

    c = prof["company"]
    kpis = prof.get("kpis", {}) or {}
    cab = [f"【{c.get('name', '?')}】"
           + (f" · CIF {c['cif']}" if c.get("cif") else "")
           + (f" · {c['company_type']}" if c.get("company_type") else "")
           + (f" · {c['status']}" if c.get("status") else "")]
    prov = ", ".join(p.get("province", "") for p in (prof.get("top_provinces") or [])[:2] if p.get("province"))
    meta = []
    if kpis.get("acts_count") is not None:
        meta.append(f"{kpis['acts_count']} actos")
    if kpis.get("first_seen") and kpis.get("last_seen"):
        meta.append(f"de {kpis['first_seen']} a {kpis['last_seen']}")
    if prov:
        meta.append(prov)
    if meta:
        cab.append(" · ".join(meta))

    off = prof.get("officers", {}) or {}
    cur = off.get("current") or []
    if cur:
        cab.append("\nCargos vigentes:")
        for o in cur[:10]:
            since = f" (desde {o['since']})" if o.get("since") else ""
            cab.append(f"  · {o.get('name', '?')} — {o.get('role', '')}{since}")
        if off.get("historical"):
            cab.append(f"  (+{len(off['historical'])} cargos históricos)")

    events = prof.get("events") or []
    if events:
        cab.append(f"\nÚltimos actos inscritos ({min(maximo_actos, len(events))} de {len(events)}):")
        for e in events[:maximo_actos]:
            det = re.sub(r"\s+", " ", (e.get("details") or e.get("type") or "")).strip()
            if len(det) > 150:
                det = det[:150] + "…"
            cab.append(f"  · {e.get('date', '?')} · {e.get('type', '')} — {det}")

    cab.append("\nNo incluye el depósito de cuentas anuales ni su contenido financiero "
               "(de pago en el Registro Mercantil). " + _FUENTE)
    return "\n".join(cab)
