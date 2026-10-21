#!/usr/bin/env python3
"""
Construye los datos de referencia por comuna (se corre una vez; el Censo no cambia):
  data/ref_comunas.csv   cut, key, comuna, region, poblacion, viviendas_ocupadas, hogares, pers_hogar
  (lat, lon = centroide de cada comuna, para las esferas del mapa)
Fuente: INE, Censo 2024, capa publica COMUNA_DPA_INDICADORES (ArcGIS FeatureServer).
El puente nombre SEC -> codigo comuna (CUT) usa 'key' de comunas.geojson (cod_comuna, key).
"""
import json, re, unicodedata
from pathlib import Path
import requests, pandas as pd

BASE = "https://services5.arcgis.com/hUyD8u3TeZLKPe4T/arcgis/rest/services/CENSO2024_V2_gdb/FeatureServer/0/query"
CAMPOS = "CUT,COMUNA,REGION,Ind_005_TPob,Ind_0010_VPO,Ind_002_THog,Ind_004_Pr_Per_Hog,Ind_001_TViv"


def norm(s):
    s = str(s).strip().lower()
    s = ''.join(ch for ch in unicodedata.normalize('NFD', s) if unicodedata.category(ch) != 'Mn')
    s = s.replace('`', "'").replace("'", '')
    return re.sub(r'\s+', ' ', s)


def consulta(**kw):
    p = {"where": "1=1", "f": "json", "resultRecordCount": 2000}
    p.update(kw)
    r = requests.get(BASE, params=p, timeout=120)
    r.raise_for_status()
    return r.json()


def main():
    att = consulta(outFields=CAMPOS, returnGeometry="false")
    df = pd.DataFrame([f["attributes"] for f in att["features"]]).rename(columns={
        "CUT": "cut", "COMUNA": "nombre_ine", "REGION": "region_ine", "Ind_005_TPob": "poblacion",
        "Ind_0010_VPO": "viviendas_ocupadas", "Ind_002_THog": "hogares", "Ind_004_Pr_Per_Hog": "pers_hogar",
        "Ind_001_TViv": "viviendas_total"})
    base = json.load(open("comunas.geojson", encoding="utf-8"))
    puente = pd.DataFrame([f["properties"] for f in base["features"]])[["cod_comuna", "key", "Comuna", "Region"]]
    puente = puente.rename(columns={"cod_comuna": "cut", "Comuna": "comuna", "Region": "region"})
    ref = df.merge(puente, on="cut", how="left")
    print("comunas INE:", len(df), "| con key SEC:", ref["key"].notna().sum())
    faltan = ref[ref["key"].isna()]
    if len(faltan):
        print("sin puente:", faltan[["cut", "nombre_ine"]].values.tolist())
    ref["key"] = ref["key"].fillna(ref["nombre_ine"].map(norm))
    ref["comuna"] = ref["comuna"].fillna(ref["nombre_ine"].str.title())
    ref["region"] = ref["region"].fillna(ref["region_ine"].str.title())
    cols = ["cut", "key", "comuna", "region", "poblacion", "viviendas_ocupadas", "hogares", "pers_hogar", "viviendas_total"]
    Path("data").mkdir(exist_ok=True)
    ref[cols].sort_values("cut").to_csv("data/ref_comunas.csv", index=False, encoding="utf-8-sig")
    print("poblacion total:", int(ref["poblacion"].sum()), "| viviendas ocupadas:", int(ref["viviendas_ocupadas"].sum()))

    # centro de cada comuna (centroide del poligono mas grande) para dibujar esferas en el mapa
    g = consulta(outFields="CUT", returnGeometry="true", outSR=4326, maxAllowableOffset=0.01,
                 geometryPrecision=4, f="geojson")
    cen = {}
    for f in g["features"]:
        geom = f["geometry"]
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        mejor = max(polys, key=lambda p: abs(area(p[0])))
        cen[f["properties"]["CUT"]] = centroide(mejor[0])
    ref["lat"] = ref["cut"].map(lambda c: cen[c][1])
    ref["lon"] = ref["cut"].map(lambda c: cen[c][0])
    ref[cols + ["lat", "lon"]].sort_values("cut").to_csv("data/ref_comunas.csv", index=False, encoding="utf-8-sig")
    print("centroides:", len(cen))


def area(r):
    return sum(r[i][0] * r[i + 1][1] - r[i + 1][0] * r[i][1] for i in range(len(r) - 1)) / 2


def centroide(r):
    a = area(r)
    if abs(a) < 1e-12:
        return round(sum(p[0] for p in r) / len(r), 4), round(sum(p[1] for p in r) / len(r), 4)
    cx = sum((r[i][0] + r[i + 1][0]) * (r[i][0] * r[i + 1][1] - r[i + 1][0] * r[i][1]) for i in range(len(r) - 1)) / (6 * a)
    cy = sum((r[i][1] + r[i + 1][1]) * (r[i][0] * r[i + 1][1] - r[i + 1][0] * r[i][1]) for i in range(len(r) - 1)) / (6 * a)
    return round(cx, 4), round(cy, 4)


if __name__ == "__main__":
    main()
