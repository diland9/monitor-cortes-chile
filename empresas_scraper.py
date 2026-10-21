#!/usr/bin/env python3
"""
Datos por distribuidora (las mismas fuentes que embebe www.sec.cl/interrupciones-en-linea):
  ENEL  -> GeoJSON por comuna con clientes totales y afectados (Region Metropolitana)
  CGE   -> KMZ de eventos (clientes afectados + restitucion estimada + poligono)
  SAESA -> KML de eventos (Saesa, Frontel, Edelaysen, Luz Osorno): comuna, estado, restitucion
  (Chilquinta requiere token/WAF; pendiente)
Escribe docs/empresas.json. Cada fuente es independiente: si una falla, las demas siguen.
Uso: python empresas_scraper.py
"""
import io, json, re, zipfile, html, datetime as dt
import xml.etree.ElementTree as ET
from pathlib import Path
import requests

OUT = Path("docs/empresas.json")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150 Safari/537.36"}
FUENTES = {
    "enel_comunas": "https://mapaemergencia.enel.com/galeria/documento/me-capa-comunasAfectadas.txt",
    "enel_estado": "https://mapaemergencia.enel.com/galeria/documento/me-capa-estado.txt",
    "cge": "https://mapa-afectaciones.grupocge.cl/afectaciones/mapa_cge.kmz",
    "saesa": "https://mfallas.saesa.cl/outage.kml",
    "saesa_futuros": "https://mfallas.saesa.cl/cortes_futuros.kml",
}
NS = {"k": "http://www.opengis.net/kml/2.2"}


def get(url):
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    return r


def parse_kml(txt):
    """Devuelve lista de eventos {nombre, estilo, desc(texto), lon, lat}."""
    root = ET.fromstring(txt.lstrip("﻿").encode("utf-8"))
    ev = []
    for pm in root.iter("{%s}Placemark" % NS["k"]):
        g = lambda tag: pm.find(".//k:%s" % tag, NS)
        d = pm.find("k:description", NS)
        desc = html.unescape(re.sub(r"<br\s*/?>", "\n", d.text or "")) if d is not None else ""
        desc = re.sub(r"<[^>]+>", "", desc)
        c = g("Point/k:coordinates") if g("Point") is not None else None
        lon = lat = None
        if c is not None and c.text:
            lon, lat = (float(x) for x in c.text.strip().split(",")[:2])
        s = pm.find("k:styleUrl", NS)
        ev.append({"estilo": (s.text if s is not None else "").lstrip("#"), "desc": desc, "lon": lon, "lat": lat})
    return ev


def campo(desc, etiqueta):
    m = re.search(etiqueta + r":\s*([^\n]+)", desc)
    return m.group(1).strip() if m else None


def num(x):
    m = re.search(r"\d[\d.]*", x or "")
    return int(m.group(0).replace(".", "")) if m else 0


def enel():
    est = get(FUENTES["enel_estado"]).json()
    gj = get(FUENTES["enel_comunas"]).json()
    comunas = []
    for f in gj["features"]:
        p = f["properties"]
        tot, afe = num(p.get("CLIENTESTOTAL")), num(p.get("CLIENTESAFECTADOS"))
        comunas.append({"comuna": p["COMUNA"].title(), "total": tot, "afectados": afe,
                        "pct": round(afe / tot * 100, 2) if tot else 0})
    comunas.sort(key=lambda c: c["afectados"], reverse=True)
    return {"actualizado": est.get("datos"), "total_clientes": sum(c["total"] for c in comunas),
            "afectados": sum(c["afectados"] for c in comunas), "comunas": comunas}


def cge():
    z = zipfile.ZipFile(io.BytesIO(get(FUENTES["cge"]).content))
    ev = parse_kml(z.read(z.namelist()[0]).decode("utf-8"))
    eventos = [{"clientes": num(campo(e["desc"], "Clientes afectados")),
                "restitucion": campo(e["desc"], "Restauraci.n estimada"),
                "confirmado": "confirmed" in e["estilo"] and "unconfirmed" not in e["estilo"],
                "lat": e["lat"], "lon": e["lon"]} for e in ev]
    return {"afectados": sum(e["clientes"] for e in eventos), "eventos": eventos}


def saesa():
    ev = parse_kml(get(FUENTES["saesa"]).content.decode("utf-8-sig"))
    eventos = [{"clientes": num(campo(e["desc"], "Clientes afectados")),
                "restitucion": campo(e["desc"], "Restauraci.n estimada"),
                "estado": campo(e["desc"], "Estado"), "comuna": (campo(e["desc"], "Comuna") or "").title(),
                "confirmado": "confirmed" in e["estilo"], "lat": e["lat"], "lon": e["lon"]} for e in ev]
    fut = parse_kml(get(FUENTES["saesa_futuros"]).content.decode("utf-8-sig"))
    programados = [{"inicio": campo(e["desc"], "Fecha inicio"), "fin": campo(e["desc"], "Fecha fin"),
                    "clientes": num(campo(e["desc"], "Clientes afectados")), "lat": e["lat"], "lon": e["lon"]}
                   for e in fut]
    return {"afectados": sum(e["clientes"] for e in eventos), "eventos": eventos, "programados": programados}


if __name__ == "__main__":
    out = {"generado": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"), "empresas": {}, "errores": {}}
    for nombre, fn in (("enel", enel), ("cge", cge), ("saesa", saesa)):
        try:
            out["empresas"][nombre] = fn()
            print(f"  {nombre}: {out['empresas'][nombre]['afectados']:,} afectados")
        except Exception as e:  # noqa  (una fuente caida no debe tumbar las demas)
            out["errores"][nombre] = str(e)[:200]
            print(f"  {nombre}: ERROR {e}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"  -> {OUT} ({OUT.stat().st_size/1024:.0f} KB)")
