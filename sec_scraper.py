#!/usr/bin/env python3
"""
Scraper de interrupciones electricas - SEC Chile
Contrato actual de los endpoints (POST, JSON), verificado en actions-page.js del sitio:
  - Get                 -> serie horaria ultimos 7 dias [{anho,mes,dia,hora,clientes_afectados}]
  - GetPorFecha         -> detalle de una hora [{NOMBRE_REGION,NOMBRE_COMUNA,CLIENTES_AFECTADOS}]
                           (body: {"anho","mes","dia","hora"})
  - GetClientesNacional -> total clientes pais [{CLIENTES}]
  - GetHoraServer       -> hora oficial del servidor [{FECHA: "dd/mm/yyyy HH:MM"}]

Salidas:
  cortes_sec.xlsx            historial acumulado (hojas: resumen, nacional, region, comuna)
  snapshot_comunas.geojson   estado actual por comuna (para mapas)
  docs/data.json             datos del dashboard (GitHub Pages)
Uso: python sec_scraper.py   (GitHub Actions, cada hora)
"""
import json, re, unicodedata, datetime as dt
from pathlib import Path
import requests, pandas as pd

BASE = "https://apps.sec.cl/INTONLINEv1/ClientesAfectados"
XLSX = Path("cortes_sec.xlsx")
GEOJSON_BASE = Path("comunas.geojson")
GEOJSON_OUT = Path("snapshot_comunas.geojson")
DATA_JSON = Path("docs/data.json")

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


def post_json(endpoint, body=None, retries=3):
    # Con cuerpo vacio hay que enviar data="" (si no, el servidor responde 411)
    data = json.dumps(body) if body is not None else ""
    last = None
    for _ in range(retries):
        try:
            r = requests.post(f"{BASE}/{endpoint}", headers=HEADERS, data=data, timeout=30)
            r.raise_for_status()
            d = r.json()
            if isinstance(d, dict):
                d = d.get("d", d)
            if isinstance(d, str):
                d = json.loads(d)
            return d
        except Exception as e:  # noqa
            last = e
    raise RuntimeError(f"Fallo {endpoint}: {last}")


def capturar():
    hora_srv = dt.datetime.strptime(post_json("GetHoraServer")[0]["FECHA"], "%d/%m/%Y %H:%M")
    ts = hora_srv.strftime("%Y-%m-%d %H:%M")
    total_pais = int(post_json("GetClientesNacional")[0]["CLIENTES"])

    serie = pd.DataFrame(post_json("Get"))
    if serie.empty:
        raise RuntimeError("SEC devolvio serie horaria vacia")
    serie = serie.sort_values(["anho", "mes", "dia", "hora"]).reset_index(drop=True)
    ult = serie.iloc[-1]
    serie["hora_ts"] = pd.to_datetime(
        dict(year=serie["anho"], month=serie["mes"], day=serie["dia"], hour=serie["hora"]))

    det = pd.DataFrame(post_json("GetPorFecha", {
        "anho": int(ult["anho"]), "mes": int(ult["mes"]),
        "dia": int(ult["dia"]), "hora": int(ult["hora"])}))
    if det.empty:
        det = pd.DataFrame(columns=["NOMBRE_REGION", "NOMBRE_COMUNA", "CLIENTES_AFECTADOS"])
    det["afectados"] = pd.to_numeric(det["CLIENTES_AFECTADOS"], errors="coerce").fillna(0)
    det["comuna"] = det["NOMBRE_COMUNA"].str.strip()
    det["key"] = det["comuna"].map(norm)
    det = det.rename(columns={"NOMBRE_REGION": "region"})
    tot = int(det["afectados"].sum())

    out = {"ts": ts, "serie": serie, "total_pais": total_pais,
           "hora_datos": f'{int(ult["dia"]):02d}/{int(ult["mes"]):02d}/{int(ult["anho"])} {int(ult["hora"]):02d}:00'}
    out["nacional"] = pd.DataFrame({"captured_at": [ts], "hora_datos": [out["hora_datos"]],
        "total_afectados": [tot], "total_clientes_pais": [total_pais],
        "pct_sin_suministro": [round(tot / total_pais * 100, 4) if total_pais else None]})
    out["region"] = det.groupby("region", as_index=False)["afectados"].sum() \
        .sort_values("afectados", ascending=False)
    out["region"].insert(0, "captured_at", ts)
    out["comuna"] = det.groupby(["region", "comuna", "key"], as_index=False)["afectados"].sum() \
        .sort_values("afectados", ascending=False)
    out["comuna"].insert(0, "captured_at", ts)
    return out


def escribir_excel(cap):
    keys = ["nacional", "region", "comuna"]
    hojas = {k: cap[k] for k in keys}
    if XLSX.exists():
        prev = pd.read_excel(XLSX, sheet_name=None)
        for k in keys:
            if prev.get(k) is not None:
                hojas[k] = pd.concat([prev[k], cap[k]], ignore_index=True)
    # evita duplicar si la misma hora se captura dos veces
    hojas["nacional"] = hojas["nacional"].drop_duplicates("captured_at", keep="last")
    hojas["region"] = hojas["region"].drop_duplicates(["captured_at", "region"], keep="last")
    hojas["comuna"] = hojas["comuna"].drop_duplicates(["captured_at", "key"], keep="last")

    nac = hojas["nacional"]
    fila = nac.iloc[-1]
    top = pd.DataFrame({"metrica": ["Ultima captura", "Primera captura", "Total capturas",
        "Afectados AHORA", "Total clientes pais", "% sin suministro AHORA"],
        "valor": [str(nac["captured_at"].max()), str(nac["captured_at"].min()), len(nac),
        int(fila["total_afectados"]), int(fila["total_clientes_pais"]),
        f'{fila["pct_sin_suministro"]}%']})

    with pd.ExcelWriter(XLSX, engine="openpyxl") as w:
        r0 = 0
        top.to_excel(w, sheet_name="resumen", index=False, startrow=r0); r0 += len(top) + 2
        pd.DataFrame([["POR REGION (AHORA)", ""]]).to_excel(w, sheet_name="resumen", index=False, header=False, startrow=r0); r0 += 1
        cap["region"][["region", "afectados"]].to_excel(w, sheet_name="resumen", index=False, startrow=r0)
        for k in keys:
            hojas[k].to_excel(w, sheet_name=k, index=False)
    print(f"  Excel -> {XLSX} ({len(nac)} capturas)")
    return hojas["nacional"]


def escribir_json(cap, nac_hist):
    DATA_JSON.parent.mkdir(parents=True, exist_ok=True)
    fila = cap["nacional"].iloc[0]
    payload = {
        "actualizado": cap["ts"],
        "hora_datos": cap["hora_datos"],
        "total_afectados": int(fila["total_afectados"]),
        "total_clientes_pais": cap["total_pais"],
        "pct_sin_suministro": fila["pct_sin_suministro"],
        "serie_7d": [{"t": r.hora_ts.strftime("%Y-%m-%dT%H:00"), "v": int(r.clientes_afectados)}
                     for r in cap["serie"].itertuples()],
        "regiones": cap["region"][["region", "afectados"]].astype({"afectados": int}).to_dict("records"),
        "comunas": cap["comuna"][["region", "comuna", "afectados"]].astype({"afectados": int})
                       .head(50).to_dict("records"),
        "capturas": [{"t": str(r.captured_at), "v": int(r.total_afectados)}
                     for r in nac_hist.itertuples()],
    }
    DATA_JSON.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"  JSON -> {DATA_JSON}")


def escribir_geojson(cap):
    if not GEOJSON_BASE.exists() or cap["comuna"].empty:
        print("  (sin geojson base o sin datos; omito geojson)"); return
    try:
        import geopandas as gpd
        geo = gpd.read_file(GEOJSON_BASE)
        m = geo.merge(cap["comuna"][["key", "afectados"]], on="key", how="left")
        m["afectados"] = m["afectados"].fillna(0)
        m.to_file(GEOJSON_OUT, driver="GeoJSON")
        print(f"  GeoJSON -> {GEOJSON_OUT}")
    except Exception as e:  # noqa  (el geojson es accesorio: no debe tumbar la captura)
        print(f"  (geojson omitido: {e})")


if __name__ == "__main__":
    cap = capturar()
    print(f"[{cap['ts']}] SEC: {int(cap['nacional'].iloc[0]['total_afectados']):,} clientes afectados "
          f"(datos de {cap['hora_datos']})")
    nac_hist = escribir_excel(cap)
    escribir_json(cap, nac_hist)
    escribir_geojson(cap)
    print("listo.")
