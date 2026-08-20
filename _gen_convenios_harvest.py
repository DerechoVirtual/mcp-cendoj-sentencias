# -*- coding: utf-8 -*-
"""Volcado del catalogo REGCON (buscador de textos) -> convenios_raw.jsonl"""
import re, sys, io, os, json, time, threading
import httpx
from concurrent.futures import ThreadPoolExecutor, as_completed

UA={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
B="https://expinterweb.mites.gob.es/regcon/pub/buscadorTextosEstatal"
RE_TOT=re.compile(r'Resultados\s*(\d+)\s*-\s*(\d+)\s*de\s*([\d\.]+)')
import html as H

AUTORIDADES = {}
for line in open('autoridades.txt', encoding='utf-8'):
    line=line.strip()
    if not line: continue
    k,v=line.split('|',1); AUTORIDADES[k]=v

OUT=os.path.abspath(sys.argv[1] if len(sys.argv)>1 else 'convenios_raw.jsonl')
AMBITOS=(sys.argv[2] if len(sys.argv)>2 else "6,5").split(',')
PALABRAS=(sys.argv[3] if len(sys.argv)>3 else "trabajo").split(',')

lock=threading.Lock()
fh=open(OUT,'a',encoding='utf-8')
seen=set()
if os.path.exists(OUT):
    pass

def parse_rows(h):
    m=re.search(r'<table[^>]*summary="Acuerdos".*?</table>',h,re.S|re.I)
    if not m: return []
    rows=re.findall(r'<tr[^>]*>(.*?)</tr>',m.group(0),re.S|re.I)
    out=[]
    for r in rows[1:]:
        cells=re.findall(r'<t[hd][^>]*>(.*?)</t[hd]>',r,re.S|re.I)
        if len(cells)<5: continue
        vals=[H.unescape(re.sub(r'\s+',' ',re.sub('<[^>]+>',' ',c))).strip() for c in cells]
        u=re.search(r'href="([^"]+)"',cells[-1])
        out.append({"codigo":vals[0],"denominacion":vals[1],"naturaleza":vals[2],
                    "autoridad":vals[3],"vigencia":vals[4] if len(vals)>4 else "",
                    "url":H.unescape(u.group(1)) if u else ""})
    return out

def total_of(h):
    t=H.unescape(re.sub('<[^>]+>',' ',h)).replace('\xa0',' ')
    m=RE_TOT.search(t)
    return int(m.group(3).replace('.','')) if m else 0

def job(al, amb, palabra):
    got=0
    try:
        with httpx.Client(headers=UA,timeout=90,follow_redirects=True) as c:
            c.get(B)
            d={"texto":palabra,"coincidencia":"1","idAutoridadLaboral":al,"idNaturaleza":"1",
               "_esNuevaBusqueda":"1","_buscar":""}
            if amb: d["idAmbitoFuncional"]=amb
            r=c.post(B,data=d)
            tot=total_of(r.text)
            if not tot: return (al,amb,palabra,0,0)
            paginas=(tot+9)//10
            allrows=parse_rows(r.text)
            for p in range(2,paginas+1):
                for intento in range(3):
                    try:
                        rp=c.get(B,params={"pagina":str(p)})
                        rows=parse_rows(rp.text)
                        if rows: allrows.extend(rows)
                        break
                    except Exception:
                        time.sleep(1.0)
            with lock:
                for row in allrows:
                    key=(row["codigo"],row["url"])
                    if key in seen: continue
                    seen.add(key)
                    row["al"]=al; row["amb"]=amb
                    fh.write(json.dumps(row,ensure_ascii=False)+"\n")
                    got+=1
                fh.flush()
            return (al,amb,palabra,tot,got)
    except Exception as e:
        return (al,amb,palabra,-1,str(e)[:80])

tareas=[(al,amb,p) for al in AUTORIDADES for amb in AMBITOS for p in PALABRAS]
print(f"tareas={len(tareas)} ambitos={AMBITOS} palabras={PALABRAS}",flush=True)
t0=time.time(); tot_rows=0
with ThreadPoolExecutor(max_workers=10) as ex:
    futs=[ex.submit(job,*t) for t in tareas]
    for i,f in enumerate(as_completed(futs),1):
        al,amb,p,tot,got=f.result()
        tot_rows+= got if isinstance(got,int) else 0
        if i%16==0 or tot==-1:
            print(f"  [{i}/{len(tareas)}] {AUTORIDADES.get(al,al)} amb={amb} tot={tot} nuevos={got} | acum={tot_rows} | {time.time()-t0:.0f}s",flush=True)
fh.close()
print(f"FIN: {tot_rows} filas nuevas en {time.time()-t0:.0f}s -> {OUT}",flush=True)
