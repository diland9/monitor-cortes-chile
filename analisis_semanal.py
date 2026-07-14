#!/usr/bin/env python3
"""
Analisis semanal - lee cortes_sec.xlsx y genera:
  - serie_nacional.csv     : evolucion del total de afectados
  - zonas_criticas.csv     : ranking de comunas por severidad (cliente-horas)
  - zonas_criticas.geojson : severidad por comuna para mapa
Correr en Colab al final de la semana.  pip install pandas openpyxl geopandas
"""
from pathlib import Path
import pandas as pd

XLSX = Path("cortes_sec.xlsx")
GEOJSON_BASE = Path("comunas.geojson")   # el mismo que usa el scraper (trae 'key')

nacional = pd.read_excel(XLSX, sheet_name="nacional")
comuna   = pd.read_excel(XLSX, sheet_name="comuna")
nacional["captured_at"] = pd.to_datetime(nacional["captured_at"])
comuna["captured_at"]   = pd.to_datetime(comuna["captured_at"])

# 1. Evolucion nacional
serie = nacional.set_index("captured_at")["total_afectados"].sort_index()
print(f"Pico nacional: {serie.max():,.0f} @ {serie.idxmax()}")
serie.to_csv("serie_nacional.csv")

# 2. Zonas criticas: cliente-horas = magnitud x duracion
comuna = comuna.sort_values("captured_at")
comuna["dt_h"] = (comuna.groupby("key")["captured_at"]
                  .diff().dt.total_seconds().div(3600).fillna(0.5))  # ~30 min
comuna["cliente_horas"] = comuna["afectados"] * comuna["dt_h"]
criticas = (comuna.groupby(["region", "comuna", "key"], as_index=False)
            .agg(pico=("afectados", "max"),
                 cliente_horas=("cliente_horas", "sum"),
                 apariciones=("afectados", "size"))
            .sort_values("cliente_horas", ascending=False))
criticas.to_csv("zonas_criticas.csv", index=False)
print("\nTop 10 comunas criticas (cliente-horas):")
print(criticas.head(10).to_string(index=False))

# 3. GeoJSON de severidad (une por 'key' normalizada)
try:
    import geopandas as gpd
    geo = gpd.read_file(GEOJSON_BASE)
    m = geo.merge(criticas.drop(columns=["region", "comuna"]), on="key", how="left")
    for c in ["pico", "cliente_horas", "apariciones"]:
        m[c] = m[c].fillna(0)
    m.to_file("zonas_criticas.geojson", driver="GeoJSON")
    print("\nzonas_criticas.geojson generado")
except Exception as e:  # noqa
    print(f"\n(sin geojson: {e})")
