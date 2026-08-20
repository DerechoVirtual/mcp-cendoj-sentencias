# -*- coding: utf-8 -*-
"""Convierte el volcado de REGCON en el indice empaquetado del conector.

Una fila del volcado = una PUBLICACION. Un convenio tiene muchas. Nos quedamos
con la mas reciente de cada codigo (las filas llegan ya de nueva a vieja) y con
el ambito funcional mas "de sector" que se le haya visto.
"""
import json, os, sys, io, re, datetime, unicodedata

def _norm(t):
    t = unicodedata.normalize('NFD', t or '')
    t = ''.join(c for c in t if unicodedata.category(c) != 'Mn')
    t = t.lower().replace('/', ' ').replace('-', ' ')
    return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9\s]', ' ', t)).strip()

SAL = os.path.join('conector', 'convenios_data')
os.makedirs(SAL, exist_ok=True)

PRIORIDAD_AMB = {"6": 0, "5": 1, "4": 2, "3": 3, "2": 4, "1": 5}

conv = {}   # codigo -> registro
orden = []

for fichero in sys.argv[1:]:
    for linea in io.open(fichero, encoding='utf-8'):
        try:
            r = json.loads(linea)
        except Exception:
            continue
        cod = (r.get('codigo') or '').strip()
        den = re.sub(r'\s+', ' ', (r.get('denominacion') or '')).strip()
        if not cod or not den or not cod.isdigit():
            continue
        url = (r.get('url') or '').strip()
        amb = r.get('amb') or '6'
        al = r.get('al') or ''
        if cod not in conv:
            conv[cod] = {'c': cod, 'd': den, 'a': al, 'm': amb, 'u': url}
            orden.append(cod)
        else:
            g = conv[cod]
            if not g['u'] and url:
                g['u'] = url
            # ambito mas cercano a "sector" gana (una publicacion puede venir
            # etiquetada distinto segun por que filtro se encontro)
            if PRIORIDAD_AMB.get(amb, 9) < PRIORIDAD_AMB.get(g['m'], 9):
                g['m'] = amb

datos = {
    'v': 1,
    'fuente': 'REGCON - Registro de convenios y acuerdos colectivos de trabajo '
              '(Ministerio de Trabajo y Economia Social)',
    'generado': datetime.date.today().isoformat(),
    'n': len(conv),
    'conv': [[conv[c]['c'], conv[c]['d'], conv[c]['a'], conv[c]['m'], conv[c]['u'], _norm(conv[c]['d'])]
             for c in orden],
}
ruta = os.path.join(SAL, 'convenios.json')
with io.open(ruta, 'w', encoding='utf-8') as f:
    json.dump(datos, f, ensure_ascii=False, separators=(',', ':'))

import collections
amb = collections.Counter(v['m'] for v in conv.values())
al = collections.Counter(v['a'] for v in conv.values())
sin_url = sum(1 for v in conv.values() if not v['u'])
print(f"{len(conv)} convenios -> {ruta} ({os.path.getsize(ruta)/1e6:.2f} MB)")
print("  por ambito:", dict(sorted(amb.items())))
print("  autoridades con datos:", len(al), "| sin URL:", sin_url)
