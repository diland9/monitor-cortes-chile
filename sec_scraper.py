#!/usr/bin/env python3
"""
Scraper de interrupciones electricas - SEC Chile
apps.sec.cl bloquea las IPs de GitHub/Google: este script se corre desde un equipo
local (ver run_local.ps1 / instalar_tarea.ps1), idealmente cada hora.

Contrato de los endpoints (POST, JSON):
  - Get                 -> serie horaria ultimos 7 dias [{anho,mes,dia,hora,clientes_afectados}]
  - GetPorFecha         -> detalle de UNA hora [{NOMBRE_REGION,NOMBRE_COMUNA,CLIENTES_AFECTADOS}]
                           (body {"anho","mes","dia","hora"}; sirve para cualquier hora de los 7 dias)
  - GetClientesNacional -> total clientes pais [{CLIENTES}]
  - GetHoraServer       -> hora oficial del servidor [{FECHA: "dd/mm/yyyy HH:MM"}]

Recuperacion: cada corrida pide el detalle de toda hora de los ultimos 7 dias que aun no
este guardada (y refresca las 2 ultimas, que pueden cambiar). Si el equipo estuvo apagado
menos de 7 dias no se pierde historial.

Salidas:
  data/nacional.csv, data/region.csv, data/comuna.csv   historial por hora (fuente de verdad)
  cortes_sec.xlsx            resumen + nacional + region + comuna (ultimos 30 dias)
  docs/data.json             datos del dashboard
  snapshot_comunas.geojson   ultima hora por comuna (si hay geopandas)
"""
import json, re, time, unicodedata, datetime as dt
from pathlib import Path
import requests, pandas as pd

BASE = "https://apps.sec.cl/INTONLINEv1/ClientesAfectados"
DATA = Path("data")
XLSX = Path("cortes_sec.xlsx")
GEOJSON_BASE = Path("comunas.geojson")
GEOJSON_OUT = Path("snapshot_comunas.geojson")
DATA_JSON = Path("docs/data.json")
MAPA_JSON = Path("docs/mapa.json")
REF_COMUNAS = Path("data/ref_comunas.csv")      # lo crea build_referencia.py (Censo 2024, INE)
# nombres que la SEC escribe distinto al INE
ALIAS = {"la calera": "calera", "marchihue": "marchigue", "paiguano": "paihuano",
         "punitagui": "punitaqui", "sierra;rda": "sierra gorda"}
REFRESCAR_ULTIMAS = 2     # horas recientes que se vuelven a pedir (el dato puede ajustarse)
DIAS_EXCEL_COMUNA = 30

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
            time.sleep(2)
    raise RuntimeError(f"Fallo {endpoint}: {last}")


def leer_csv(nombre, cols):
    p = DATA / f"{nombre}.csv"
    if p.exists():
        return pd.read_csv(p, encoding="utf-8-sig", dtype={"hora": str})
    return pd.DataFrame(columns=cols)


def detalle_hora(h):
    """h: Timestamp de la hora. Devuelve (region_df, comuna_df) de esa hora."""
    det = pd.DataFrame(post_json("GetPorFecha", {"anho": h.year, "mes": h.month, "dia": h.day, "hora": h.hour}))
    hs = h.strftime("%Y-%m-%d %H:00")
    if det.empty:
        return (pd.DataFrame(columns=["hora", "region", "afectados"]),
                pd.DataFrame(columns=["hora", "region", "comuna", "key", "afectados"]))
    det["afectados"] = pd.to_numeric(det["CLIENTES_AFECTADOS"], errors="coerce").fillna(0).astype(int)
    det["comuna"] = det["NOMBRE_COMUNA"].str.strip()
    det["key"] = det["comuna"].map(norm)
    det = det.rename(columns={"NOMBRE_REGION": "region"})
    reg = det.groupby("region", as_index=False)["afectados"].sum().sort_values("afectados", ascending=False)
    com = det.groupby(["region", "comuna", "key"], as_index=False)["afectados"].sum() \
        .sort_values("afectados", ascending=False)
    reg.insert(0, "hora", hs)
    com.insert(0, "hora", hs)
    return reg, com


def actualizar():
    ts = dt.datetime.strptime(post_json("GetHoraServer")[0]["FECHA"], "%d/%m/%Y %H:%M")
    total_pais = int(post_json("GetClientesNacional")[0]["CLIENTES"])
    serie = pd.DataFrame(post_json("Get"))
    if serie.empty:
        raise RuntimeError("SEC devolvio serie horaria vacia")
    serie["hora_ts"] = pd.to_datetime(
        dict(year=serie["anho"], month=serie["mes"], day=serie["dia"], hour=serie["hora"]))
    serie = serie.sort_values("hora_ts").reset_index(drop=True)
    serie["hkey"] = serie["hora_ts"].dt.strftime("%Y-%m-%d %H:00")

    cols_n = ["hora", "total_afectados", "total_clientes_pais", "pct_sin_suministro", "capturado"]
    nac = leer_csv("nacional", cols_n)
    reg = leer_csv("region", ["hora", "region", "afectados"])
    com = leer_csv("comuna", ["hora", "region", "comuna", "key", "afectados"])

    guardadas = set(nac["hora"])
    ultimas = set(serie["hkey"].tail(REFRESCAR_ULTIMAS))
    pendientes = serie[~serie["hkey"].isin(guardadas) | serie["hkey"].isin(ultimas)]
    print(f"  serie: {len(serie)} h | pendientes/refresco: {len(pendientes)}")

    nuevas_reg, nuevas_com, nuevas_nac = [], [], []
    for r in pendientes.itertuples():
        rg, cm = detalle_hora(r.hora_ts)
        nuevas_reg.append(rg)
        nuevas_com.append(cm)
        tot = int(r.clientes_afectados)
        nuevas_nac.append({"hora": r.hkey, "total_afectados": tot, "total_clientes_pais": total_pais,
                           "pct_sin_suministro": round(tot / total_pais * 100, 4) if total_pais else None,
                           "capturado": ts.strftime("%Y-%m-%d %H:%M")})
        time.sleep(0.15)

    horas = set(pendientes["hkey"])
    def unir(hist, nuevas, orden):
        # orden determinista: si no, el CSV se reordena en cada corrida y git lo ve como cambio total
        base = hist[~hist["hora"].isin(horas)]
        todo = pd.concat([base] + [n for n in nuevas if len(n)], ignore_index=True)
        return todo.sort_values(orden, kind="mergesort").reset_index(drop=True)
    nac = unir(nac, [pd.DataFrame(nuevas_nac)], ["hora"])
    reg = unir(reg, nuevas_reg, ["hora", "region"])
    com = unir(com, nuevas_com, ["hora", "region", "comuna"])

    DATA.mkdir(exist_ok=True)
    nac.to_csv(DATA / "nacional.csv", index=False, encoding="utf-8-sig")
    reg.to_csv(DATA / "region.csv", index=False, encoding="utf-8-sig")
    com.to_csv(DATA / "comuna.csv", index=False, encoding="utf-8-sig")
    return ts, total_pais, serie, nac, reg, com


def escribir_excel(nac, reg, com):
    ult = nac.iloc[-1]
    top = pd.DataFrame({"metrica": ["Ultima hora con datos", "Primera hora guardada", "Horas guardadas",
        "Afectados (ultima hora)", "Total clientes pais", "% sin suministro"],
        "valor": [ult["hora"], nac["hora"].iloc[0], len(nac), int(ult["total_afectados"]),
                  int(ult["total_clientes_pais"]), f'{ult["pct_sin_suministro"]}%']})
    reg_now = reg[reg["hora"] == ult["hora"]].sort_values("afectados", ascending=False)[["region", "afectados"]]
    corte = (pd.Timestamp(ult["hora"]) - pd.Timedelta(days=DIAS_EXCEL_COMUNA)).strftime("%Y-%m-%d %H:00")
    with pd.ExcelWriter(XLSX, engine="openpyxl") as w:
        top.to_excel(w, sheet_name="resumen", index=False)
        pd.DataFrame([["POR REGION (ultima hora)", ""]]).to_excel(
            w, sheet_name="resumen", index=False, header=False, startrow=len(top) + 2)
        reg_now.to_excel(w, sheet_name="resumen", index=False, startrow=len(top) + 3)
        nac.to_excel(w, sheet_name="nacional", index=False)
        reg.to_excel(w, sheet_name="region", index=False)
        com[com["hora"] >= corte].to_excel(w, sheet_name="comuna", index=False)
    print(f"  Excel -> {XLSX} ({len(nac)} horas)")


def escribir_json(ts, total_pais, serie, nac, reg, com):
    DATA_JSON.parent.mkdir(parents=True, exist_ok=True)
    ult_h = serie["hkey"].iloc[-1]
    fila = nac[nac["hora"] == ult_h].iloc[0]
    u = serie.iloc[-1]
    hist = nac.tail(24 * 30)
    payload = {
        "actualizado": ts.strftime("%Y-%m-%d %H:%M"),
        "hora_datos": f'{int(u["dia"]):02d}/{int(u["mes"]):02d}/{int(u["anho"])} {int(u["hora"]):02d}:00',
        "total_afectados": int(fila["total_afectados"]),
        "total_clientes_pais": total_pais,
        "pct_sin_suministro": float(fila["pct_sin_suministro"]),
        "serie_7d": [{"t": r.hkey.replace(" ", "T"), "v": int(r.clientes_afectados)} for r in serie.itertuples()],
        "regiones": reg[reg["hora"] == ult_h].sort_values("afectados", ascending=False)[["region", "afectados"]].to_dict("records"),
        "comunas": com[com["hora"] == ult_h].sort_values("afectados", ascending=False)[["region", "comuna", "afectados"]].head(50).to_dict("records"),
        "capturas": [{"t": r.hora, "v": int(r.total_afectados)} for r in hist.itertuples()],
    }
    DATA_JSON.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"  JSON -> {DATA_JSON}")


def escribir_mapa(ts, total_pais, serie, com):
    """Datos del mapa: por comuna, clientes afectados por hora (7 dias) + denominadores estimados.
    Clientes totales de la comuna (estimado) = viviendas ocupadas (Censo 2024) x k, con
    k = clientes del pais (SEC) / viviendas ocupadas del pais, para que sume el total real de la SEC.
    Personas sin suministro (estimado) = clientes afectados x (poblacion / clientes totales)."""
    if not REF_COMUNAS.exists():
        print("  (sin data/ref_comunas.csv: corre build_referencia.py; omito mapa)")
        return
    ref = pd.read_csv(REF_COMUNAS, encoding="utf-8-sig")
    k = total_pais / ref["viviendas_ocupadas"].sum()
    ref["cli"] = (ref["viviendas_ocupadas"] * k).round().astype(int)
    # comunas sin viviendas ocupadas en el Censo (p. ej. Antartica): usa el promedio nacional de personas por cliente
    sin_viv = ref["cli"] < 1
    ref.loc[sin_viv, "cli"] = (ref.loc[sin_viv, "poblacion"] / (ref["poblacion"].sum() / total_pais)).round().clip(lower=1)
    ref["cli"] = ref["cli"].astype(int)
    ref["ph"] = (ref["poblacion"] / ref["cli"]).round(3)
    horas = list(serie["hkey"])
    c = com[com["hora"].isin(horas)].copy()
    c["key"] = c["key"].replace(ALIAS)
    c = c.merge(ref[["key", "cut"]], on="key", how="left")
    sin = c[c["cut"].isna()]["key"].unique()
    if len(sin):
        print(f"  aviso: comunas SEC sin match en el Censo: {list(sin)}")
    mat = c.dropna(subset=["cut"]).pivot_table(index="cut", columns="hora", values="afectados",
                                               aggfunc="sum", fill_value=0).reindex(columns=horas, fill_value=0)
    comunas = []
    for r in ref.itertuples():
        a = mat.loc[r.cut].astype(int).tolist() if r.cut in mat.index else [0] * len(horas)
        comunas.append({"cut": int(r.cut), "n": r.comuna, "r": r.region, "pob": int(r.poblacion),
                        "cli": int(r.cli), "ph": float(r.ph), "a": a})
    u = serie.iloc[-1]
    out = {"actualizado": ts.strftime("%Y-%m-%d %H:%M"),
           "hora_datos": f'{int(u["dia"]):02d}/{int(u["mes"]):02d}/{int(u["anho"])} {int(u["hora"]):02d}:00',
           "total_pais": total_pais, "k": round(k, 4), "poblacion_pais": int(ref["poblacion"].sum()),
           "horas": [h.replace(" ", "T") for h in horas],
           "serie": [int(v) for v in serie["clientes_afectados"]], "comunas": comunas}
    MAPA_JSON.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"  Mapa -> {MAPA_JSON} ({MAPA_JSON.stat().st_size/1024:.0f} KB, k={k:.3f})")


def escribir_geojson(com, ult_h):
    ahora = com[com["hora"] == ult_h]
    if not GEOJSON_BASE.exists() or ahora.empty:
        return
    try:
        import geopandas as gpd
        geo = gpd.read_file(GEOJSON_BASE)
        m = geo.merge(ahora[["key", "afectados"]], on="key", how="left")
        m["afectados"] = m["afectados"].fillna(0)
        m.to_file(GEOJSON_OUT, driver="GeoJSON")
        print(f"  GeoJSON -> {GEOJSON_OUT}")
    except Exception as e:  # noqa  (accesorio: no debe tumbar la captura)
        print(f"  (geojson omitido: {e})")


if __name__ == "__main__":
    ts, total_pais, serie, nac, reg, com = actualizar()
    ult = nac.iloc[-1]
    print(f"[{ts:%Y-%m-%d %H:%M}] SEC: {int(ult['total_afectados']):,} clientes afectados (hora {ult['hora']})")
    escribir_excel(nac, reg, com)
    escribir_json(ts, total_pais, serie, nac, reg, com)
    escribir_mapa(ts, total_pais, serie, com)
    escribir_geojson(com, serie["hkey"].iloc[-1])
    print("listo.")
