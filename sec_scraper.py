#!/usr/bin/env python3
"""
Scraper de interrupciones electricas - SEC Chile
Rutas MVC verificadas (Copy as cURL): POST con cuerpo vacio.
  - GetClientesNacional -> total clientes pais
  - Get                 -> detalle actual por comuna/empresa

Escribe un mismo Excel (cortes_sec.xlsx, append) con 7 hojas:
  resumen | nacional | region | comuna | nacional_empresa | region_empresa | comuna_empresa
Sobrescribe snapshot_comunas.geojson con el estado actual (para mapas).
Uso: python sec_scraper.py   (GitHub Actions cada 30 min)
"""
import json, re, unicodedata, datetime as dt
from pathlib import Path
import requests, pandas as pd

BASE = "https://apps.sec.cl/INTONLINEv1/ClientesAfectados"
XLSX = Path("cortes_sec.xlsx")
GEOJSON_BASE = Path("comunas.geojson")
GEOJSON_OUT  = Path("snapshot_comunas.geojson")
DIAS_VENTANA = 7   # descarta eventos colgados de mas de N dias

ENDPOINTS = {"detalle": f"{BASE}/Get", "nacional": f"{BASE}/GetClientesNacional"}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/150 Safari/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/json; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://apps.sec.cl/INTONLINEv1/index.aspx",
    "Origin": "https://apps.sec.cl",
}


def norm(s):
    s = str(s).strip().lower()
    s = ''.join(ch for ch in unicodedata.normalize('NFD', s) if unicodedata.category(ch) != 'Mn')
    s = s.replace('`', "'").replace("'", '')
    return re.sub(r'\s+', ' ', s)


def post_json(url, retries=3):
    last = None
    for _ in range(retries):
        try:
            r = requests.post(url, headers=HEADERS, data="", timeout=30)
            r.raise_for_status()
            data = r.json()
            d = data.get("d", data) if isinstance(data, dict) else data
            if isinstance(d, str):
                d = json.loads(d)
            return d
        except Exception as e:  # noqa
            last = e
    raise RuntimeError(f"Fallo {url}: {last}")


def capturar(ts, hoy):
    nac_raw = pd.json_normalize(post_json(ENDPOINTS["nacional"]))
    total_pais = int(pd.to_numeric(nac_raw.get("CLIENTES"), errors="coerce").sum())

    det = pd.json_normalize(post_json(ENDPOINTS["detalle"]))
    det["afectados"] = pd.to_numeric(det["CLIENTES_AFECTADOS"], errors="coerce")
    det["fecha_evento"] = pd.to_datetime(
        dict(year=det["ANHO_INT"], month=det["MES_INT"], day=det["DIA_INT"]), errors="coerce")
    det = det[det["fecha_evento"] >= pd.Timestamp(hoy) - pd.Timedelta(days=DIAS_VENTANA)].copy()
    det["comuna"] = det["NOMBRE_COMUNA"].str.strip()
    det["key"] = det["comuna"].map(norm)
    det = det.rename(columns={"NOMBRE_REGION": "region", "NOMBRE_EMPRESA": "empresa"})
    tot = int(det["afectados"].sum())

    out = {}
    # Totales
    out["nacional"] = pd.DataFrame({"captured_at": [ts], "total_afectados": [tot],
        "total_clientes_pais": [total_pais],
        "pct_sin_suministro": [round(tot/total_pais*100, 4) if total_pais else None]})
    out["region"] = det.groupby("region", as_index=False)["afectados"].sum() \
        .sort_values("afectados", ascending=False); out["region"].insert(0, "captured_at", ts)
    out["comuna"] = det.groupby(["region", "comuna", "key"], as_index=False)["afectados"].sum() \
        .sort_values("afectados", ascending=False); out["comuna"].insert(0, "captured_at", ts)
    # Desglose por empresa
    out["nacional_empresa"] = det.groupby("empresa", as_index=False)["afectados"].sum() \
        .sort_values("afectados", ascending=False); out["nacional_empresa"].insert(0, "captured_at", ts)
    out["region_empresa"] = det.groupby(["region", "empresa"], as_index=False)["afectados"].sum() \
        .sort_values(["region", "afectados"], ascending=[True, False]); out["region_empresa"].insert(0, "captured_at", ts)
    out["comuna_empresa"] = det.groupby(["region", "comuna", "key", "empresa"], as_index=False)["afectados"].sum() \
        .sort_values("afectados", ascending=False); out["comuna_empresa"].insert(0, "captured_at", ts)
    return out


def build_resumen(nacional_hist, reg_now, emp_now):
    ult = nacional_hist["captured_at"].max()
    fila = nacional_hist.iloc[-1]
    top = pd.DataFrame({"metrica": ["Ultima captura", "Primera captura", "Total capturas",
        "Afectados AHORA", "Total clientes pais", "% sin suministro AHORA"],
        "valor": [ult, nacional_hist["captured_at"].min(), len(nacional_hist),
        int(fila["total_afectados"]), int(fila["total_clientes_pais"]),
        f'{fila["pct_sin_suministro"]}%']})
    return top


def escribir_excel(cap):
    keys = ["nacional", "region", "comuna", "nacional_empresa", "region_empresa", "comuna_empresa"]
    hojas = {k: cap[k] for k in keys}
    if XLSX.exists():
        prev = pd.read_excel(XLSX, sheet_name=None)
        for k in keys:
            if prev.get(k) is not None:
                hojas[k] = pd.concat([prev[k], cap[k]], ignore_index=True)
    hojas["nacional"]["captured_at"] = pd.to_datetime(hojas["nacional"]["captured_at"])

    top = build_resumen(hojas["nacional"], cap["region"], cap["nacional_empresa"])
    reg_now = cap["region"][["region", "afectados"]].reset_index(drop=True)
    emp_now = cap["nacional_empresa"][["empresa", "afectados"]].reset_index(drop=True)

    with pd.ExcelWriter(XLSX, engine="openpyxl") as w:
        r0 = 0
        top.to_excel(w, sheet_name="resumen", index=False, startrow=r0); r0 += len(top) + 2
        pd.DataFrame([["POR REGION (AHORA)", ""]]).to_excel(w, sheet_name="resumen", index=False, header=False, startrow=r0); r0 += 1
        reg_now.to_excel(w, sheet_name="resumen", index=False, startrow=r0); r0 += len(reg_now) + 2
        pd.DataFrame([["POR EMPRESA (AHORA)", ""]]).to_excel(w, sheet_name="resumen", index=False, header=False, startrow=r0); r0 += 1
        emp_now.to_excel(w, sheet_name="resumen", index=False, startrow=r0)
        for k in keys:
            hojas[k].to_excel(w, sheet_name=k, index=False)
    print(f"  Excel -> {XLSX} ({len(hojas['nacional'])} capturas)")


def escribir_geojson(cap):
    if not GEOJSON_BASE.exists() or cap["comuna"].empty:
        print("  (sin geojson base o sin datos; omito geojson)"); return
    import geopandas as gpd
    geo = gpd.read_file(GEOJSON_BASE)
    m = geo.merge(cap["comuna"][["key", "afectados"]], on="key", how="left")
    m["afectados"] = m["afectados"].fillna(0)
    m.to_file(GEOJSON_OUT, driver="GeoJSON")
    print(f"  GeoJSON -> {GEOJSON_OUT}")


if __name__ == "__main__":
    now = dt.datetime.now(dt.timezone.utc).astimezone()
    ts = now.strftime("%Y-%m-%d %H:%M")
    print(f"[{ts}] capturando SEC...")
    cap = capturar(ts, now.date())
    escribir_excel(cap)
    escribir_geojson(cap)
    print("listo.")
