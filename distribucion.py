#!/usr/bin/env python3
"""
Monitor distribucion: clientes sin suministro por distribuidora y comuna.

Fuentes (las mismas que embebe www.sec.cl/interrupciones-en-linea; solo entregan el estado ACTUAL,
por eso guardamos nuestro propio historial):
  ENEL    GeoJSON por comuna con clientes totales y afectados (Region Metropolitana)
  CGE     KMZ de eventos (clientes, restitucion estimada, poligono); la comuna se deduce del punto
  SAESA   KML de eventos del grupo (Saesa, Frontel, Edelaysen, Luz Osorno), con comuna
  "Chilquinta y otras (por diferencia)" = total SEC de la comuna - (ENEL + CGE + SAESA)

Uso:
  python distribucion.py --fuente nube --sin-construir   # GitHub Actions: solo agrega al historial
  python distribucion.py --fuente local                  # PC: agrega al historial y reconstruye docs/dist.json
  python distribucion.py --solo-construir                # solo reconstruye docs/dist.json

Historial (sin conflictos entre nube y PC, cada uno escribe su archivo):
  data/dist_nube.csv, data/dist_local.csv   captura, hora, distribuidora, cut, comuna, afectados, eventos, clientes_total
    (fila con cut=0 = marca de captura + total de la distribuidora en esa captura)
Salidas:
  docs/dist.json    series por hora (7 dias), por distribuidora y comuna, para docs/distribucion.html
"""
import argparse, csv, html, io, json, re, unicodedata, zipfile, datetime as dt
import xml.etree.ElementTree as ET
from pathlib import Path
import requests, pandas as pd

DATA, DOCS = Path("data"), Path("docs")
REF, GEO = DATA / "ref_comunas.csv", DATA / "comunas_geo.json"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150 Safari/537.36"}
FUENTES = {
    "enel_comunas": "https://mapaemergencia.enel.com/galeria/documento/me-capa-comunasAfectadas.txt",
    "cge": "https://mapa-afectaciones.grupocge.cl/afectaciones/mapa_cge.kmz",
    "saesa": "https://mfallas.saesa.cl/outage.kml",
}
NS = {"k": "http://www.opengis.net/kml/2.2"}
ALIAS = {"la calera": "calera", "marchihue": "marchigue", "paiguano": "paihuano", "punitagui": "punitaqui", "sierra;rda": "sierra gorda"}
CONOCIDAS = ["ENEL", "CGE", "SAESA"]
NH = 168                                  # horas de historia en el dashboard (7 dias)
COLS = ["captura", "hora", "distribuidora", "cut", "comuna", "afectados", "eventos", "clientes_total"]


def norm(s):
    s = str(s).strip().lower()
    s = ''.join(ch for ch in unicodedata.normalize('NFD', s) if unicodedata.category(ch) != 'Mn')
    s = s.replace('`', "'").replace("'", '')
    return re.sub(r'[\s_]+', ' ', s)


def ahora():
    """Hora de Chile (la nube corre en UTC; en el PC, si falta tzdata, usa la hora local del equipo)."""
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo("America/Santiago")).replace(tzinfo=None)
    except Exception:  # noqa
        return dt.datetime.now()


def get(url):
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    return r


def num(x):
    m = re.search(r"\d[\d.]*", str(x or ""))
    return int(m.group(0).replace(".", "")) if m else 0


# ---------------- geometria ----------------
class Geo:
    def __init__(self, ref):
        self.polys = []
        if GEO.exists():
            for cut, polys in json.loads(GEO.read_text(encoding="utf-8")).items():
                for ring in polys:
                    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
                    self.polys.append((int(cut), ring, (min(xs), min(ys), max(xs), max(ys))))
        self.cent = [(int(r.cut), r.lon, r.lat) for r in ref.itertuples()]

    @staticmethod
    def dentro(x, y, ring):
        c, j = False, len(ring) - 1
        for i in range(len(ring)):
            xi, yi, xj, yj = ring[i][0], ring[i][1], ring[j][0], ring[j][1]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi:
                c = not c
            j = i
        return c

    def comuna(self, lon, lat):
        for cut, ring, bb in self.polys:
            if bb[0] <= lon <= bb[2] and bb[1] <= lat <= bb[3] and self.dentro(lon, lat, ring):
                return cut
        return min(self.cent, key=lambda c: (c[1] - lon) ** 2 + (c[2] - lat) ** 2)[0]     # respaldo: centroide mas cercano


def parse_kml(txt):
    root = ET.fromstring(txt.lstrip("﻿").encode("utf-8"))
    ev = []
    for pm in root.iter("{%s}Placemark" % NS["k"]):
        d = pm.find("k:description", NS)
        desc = html.unescape(re.sub(r"<br\s*/?>", "\n", d.text or "")) if d is not None else ""
        desc = re.sub(r"<[^>]+>", "", desc)
        pt = pm.find(".//k:Point/k:coordinates", NS)
        lon = lat = None
        if pt is not None and pt.text:
            lon, lat = (float(x) for x in pt.text.strip().split(",")[:2])
        poly = []
        pc = pm.find(".//k:Polygon//k:coordinates", NS)
        if pc is not None and pc.text:
            pts = [tuple(float(v) for v in t.split(",")[:2]) for t in pc.text.split()]
            paso = max(1, len(pts) // 24)
            poly = [[round(y, 4), round(x, 4)] for x, y in pts[::paso]]
        s = pm.find("k:styleUrl", NS)
        ev.append({"estilo": (s.text if s is not None else "").lstrip("#"), "desc": desc, "lon": lon, "lat": lat, "poly": poly})
    return ev


def campo(desc, etiqueta):
    m = re.search(etiqueta + r":\s*([^\n]+)", desc)
    return m.group(1).strip() if m else None


# ---------------- fuentes ----------------
def f_enel(geo, keymap):
    gj = get(FUENTES["enel_comunas"]).json()
    por, ctot = {}, {}
    for f in gj["features"]:
        p = f["properties"]
        m = re.match(r"\d+", str(p.get("COD_VNR", "")))      # algunos codigos traen sufijo (p. ej. 13301C): se suman a la comuna
        if not m:
            continue
        cut, tot, afe = int(m.group(0)), num(p.get("CLIENTESTOTAL")), num(p.get("CLIENTESAFECTADOS"))
        ctot[cut] = ctot.get(cut, 0) + tot
        por[cut] = por.get(cut, 0) + afe
    filas = [{"cut": c, "afectados": a, "eventos": 0, "clientes_total": ctot[c]} for c, a in por.items() if a > 0]
    return {"total": sum(por.values()), "filas": filas, "areas": [], "clientes_total": ctot}


def f_eventos(emp, eventos, geo, keymap, por_nombre):
    por, areas, total = {}, [], 0
    for e in eventos:
        n = num(campo(e["desc"], "Clientes afectados"))
        cut = None
        if por_nombre:
            nom = campo(e["desc"], "Comuna")
            if nom:
                k = ALIAS.get(norm(nom), norm(nom))
                cut = keymap.get(k)
        if cut is None and e["lon"] is not None:
            cut = geo.comuna(e["lon"], e["lat"])
        if cut is None:
            continue
        r = por.setdefault(cut, {"cut": cut, "afectados": 0, "eventos": 0, "clientes_total": ""})
        r["afectados"] += n
        r["eventos"] += 1
        total += n
        areas.append({"emp": emp, "cut": cut, "clientes": n, "restitucion": campo(e["desc"], "Restauraci.n estimada"),
                      "estado": campo(e["desc"], "Estado"), "confirmado": "confirmed" in e["estilo"] and "unconfirmed" not in e["estilo"],
                      "lat": e["lat"], "lon": e["lon"], "poly": e["poly"]})
    return {"total": total, "filas": list(por.values()), "areas": areas, "clientes_total": {}}


def f_cge(geo, keymap):
    z = zipfile.ZipFile(io.BytesIO(get(FUENTES["cge"]).content))
    return f_eventos("CGE", parse_kml(z.read(z.namelist()[0]).decode("utf-8")), geo, keymap, False)


def f_saesa(geo, keymap):
    return f_eventos("SAESA", parse_kml(get(FUENTES["saesa"]).content.decode("utf-8-sig")), geo, keymap, True)


# ---------------- recoleccion ----------------
def colectar(fuente):
    DATA.mkdir(exist_ok=True)
    ref = pd.read_csv(REF, encoding="utf-8-sig")
    keymap, nombre = dict(zip(ref["key"], ref["cut"])), dict(zip(ref["cut"], ref["comuna"]))
    geo = Geo(ref)
    t = ahora()
    captura, hora = t.strftime("%Y-%m-%d %H:%M"), t.strftime("%Y-%m-%d %H:00")
    filas, areas, estado = [], [], {}
    for emp, fn in (("ENEL", f_enel), ("CGE", f_cge), ("SAESA", f_saesa)):
        try:
            r = fn(geo, keymap)
        except Exception as e:  # noqa  (una fuente caida no tumba a las demas)
            estado[emp] = f"ERROR {str(e)[:120]}"
            print(f"  {emp}: ERROR {e}")
            continue
        estado[emp] = r["total"]
        print(f"  {emp}: {r['total']:,} clientes afectados ({len(r['filas'])} comunas)")
        filas.append([captura, hora, emp, 0, "*TOTAL*", r["total"], len(r["filas"]), ""])
        for x in r["filas"]:
            filas.append([captura, hora, emp, x["cut"], nombre.get(x["cut"], ""), x["afectados"], x["eventos"], x["clientes_total"]])
        areas += r["areas"]
        if r["clientes_total"] and not (DATA / "enel_clientes.json").exists():
            (DATA / "enel_clientes.json").write_text(json.dumps({str(k): v for k, v in r["clientes_total"].items()}), encoding="utf-8")
    if filas:
        p = DATA / f"dist_{fuente}.csv"
        nuevo = not p.exists()
        with open(p, "a", newline="", encoding="utf-8-sig" if nuevo else "utf-8") as fh:
            w = csv.writer(fh)
            if nuevo:
                w.writerow(COLS)
            w.writerows(filas)
        (DATA / f"dist_areas_{fuente}.json").write_text(
            json.dumps({"captura": captura, "areas": areas}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"  -> {p} (+{len(filas)} filas) | areas: {len(areas)}")
    return captura


# ---------------- construccion del dashboard ----------------
def construir():
    ref = pd.read_csv(REF, encoding="utf-8-sig")
    keymap, info = dict(zip(ref["key"], ref["cut"])), ref.set_index("cut")
    frames = [pd.read_csv(DATA / f"dist_{f}.csv", encoding="utf-8-sig") for f in ("nube", "local") if (DATA / f"dist_{f}.csv").exists()]
    if not frames:
        print("  (sin historial de distribuidoras todavia)")
        return
    df = pd.concat(frames, ignore_index=True).sort_values("captura")
    df = df.drop_duplicates(["hora", "distribuidora", "cut"], keep="last")
    ultima = pd.Timestamp(df["hora"].max())
    hs = [h.strftime("%Y-%m-%d %H:00") for h in pd.date_range(end=ultima, periods=NH, freq="h")]
    idx = {h: i for i, h in enumerate(hs)}
    df = df[df["hora"].isin(idx)]

    tot = {e: [None] * NH for e in CONOCIDAS}
    for r in df[df["cut"] == 0].itertuples():
        tot[r.distribuidora][idx[r.hora]] = int(r.afectados)
    ser = {}
    for r in df[df["cut"] != 0].itertuples():
        ser.setdefault(int(r.cut), {}).setdefault(r.distribuidora, [None] * NH)[idx[r.hora]] = int(r.afectados)
    for d in ser.values():                      # captura hecha y la comuna no aparece = 0 clientes
        for e, arr in d.items():
            for i in range(NH):
                if arr[i] is None and tot[e][i] is not None:
                    arr[i] = 0

    # SEC (oficial) por comuna y total nacional
    nac = pd.read_csv(DATA / "nacional.csv", encoding="utf-8-sig").set_index("hora")["total_afectados"]
    com = pd.read_csv(DATA / "comuna.csv", encoding="utf-8-sig")
    com = com[com["hora"].isin(idx)].copy()
    com["cut"] = com["key"].replace(ALIAS).map(keymap)
    sec = {}
    for r in com.dropna(subset=["cut"]).itertuples():
        sec.setdefault(int(r.cut), [None] * NH)[idx[r.hora]] = int(r.afectados)
    completa = [all(tot[e][i] is not None for e in CONOCIDAS) for i in range(NH)]
    sec_tot = [int(nac[h]) if h in nac.index else None for h in hs]
    tot["OTRAS"] = [max(0, sec_tot[i] - sum(tot[e][i] for e in CONOCIDAS)) if completa[i] and sec_tot[i] is not None else None for i in range(NH)]
    tot["SEC"] = sec_tot
    for cut, sarr in sec.items():
        arr = [None] * NH
        for i in range(NH):
            if completa[i]:
                conocido = sum((ser.get(cut, {}).get(e, [0] * NH)[i] or 0) for e in CONOCIDAS)
                arr[i] = max(0, (sarr[i] or 0) - conocido)
        if any(arr):
            ser.setdefault(cut, {})["OTRAS"] = arr

    enel_ct = json.loads((DATA / "enel_clientes.json").read_text(encoding="utf-8")) if (DATA / "enel_clientes.json").exists() else {}
    comunas = {}
    for cut, r in info.iterrows():           # todas las comunas (el buscador debe encontrar cualquiera, aunque hoy no tenga cortes)
        comunas[str(cut)] = {"n": r["comuna"], "r": r["region"], "lat": float(r["lat"]), "lon": float(r["lon"]),
                             "pob": int(r["poblacion"]), "ct": enel_ct.get(str(cut))}
    fuentes = {}
    for e in CONOCIDAS:
        m = df[(df["cut"] == 0) & (df["distribuidora"] == e)]
        fuentes[e] = {"captura": m["captura"].max() if len(m) else None}
    areas = []
    arch = [json.loads(p.read_text(encoding="utf-8")) for p in DATA.glob("dist_areas_*.json")]
    if arch:
        areas = max(arch, key=lambda a: a["captura"])
    out = {"actualizado": str(df["captura"].max()), "horas": [h.replace(" ", "T") for h in hs], "totales": tot,
           "fuentes": fuentes, "comunas": comunas, "series": {str(k): v for k, v in ser.items()},
           "areas": areas.get("areas", []) if areas else [], "areas_captura": areas.get("captura") if areas else None}
    DOCS.mkdir(exist_ok=True)
    (DOCS / "dist.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    p = DOCS / "dist.json"
    print(f"  dist.json -> {p} ({p.stat().st_size/1024:.0f} KB, {len(comunas)} comunas, {sum(1 for h in hs if any(tot[e][hs.index(h)] is not None for e in CONOCIDAS))} horas con datos)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fuente", choices=["nube", "local"], default="local")
    ap.add_argument("--sin-construir", action="store_true")
    ap.add_argument("--solo-construir", action="store_true")
    a = ap.parse_args()
    if not a.solo_construir:
        print(f"[{colectar(a.fuente)}] captura distribuidoras ({a.fuente})")
    if not a.sin_construir:
        construir()
