"""
Publicador de datos para la app "Alertas Lagos".

Este script NO corre dentro de la app: corre en un cron de GitHub Actions
(ver .github/workflows/actualizar-datos.yml), una sola vez por intervalo,
sin importar cuántas personas del equipo tengan la app abierta. Consulta
CEN + DGA + Open-Meteo y escribe el resultado en docs/datos.json, que
GitHub Pages sirve como una URL pública fija.

Por qué existe este script separado:
La app original le pegaba a CEN/DGA/Open-Meteo directo desde cada celular.
El token del CEN tiene un límite de 60 consultas/hora COMPARTIDO entre
todo el que use la app (porque el user_key va hardcodeado en el cliente).
Con este esquema, sin importar si son 3 personas o 30, el CEN solo recibe
UNA consulta cada vez que corre este cron (por defecto cada 30 min) -> 48
consultas/día como máximo, muy lejos del límite. Como bonus, el
CEN_USER_KEY ya no viaja dentro del APK: vive únicamente como secret de
GitHub Actions, así que no queda expuesto si alguien descompila la app.

LAKES_METADATA debe mantenerse EN SINCRONÍA (mismos nombres de lago como
llave) con el diccionario homónimo del repo de la app (main.py). Si agregas
o quitas un lago, hazlo en ambos lados.
"""
import requests
import json
import os
import sys
import unicodedata
from datetime import datetime, timezone

LAKES_METADATA = {
    "Rapel": {"source": "CEN", "api_name": "rapel", "lat": -34.17, "lon": -71.49},
    "Vichuquén": {"source": "DGA", "api_name": "lago vichuquen", "lat": -34.88, "lon": -72.03},
    "Colbún": {"source": "CEN", "api_name": "colbun", "lat": -35.68, "lon": -71.36},
    "Villarrica": {"source": "DGA", "api_name": "lago villarrica en sector la poza", "lat": -39.28, "lon": -72.22},
    "Caburgua": {"source": "DGA", "api_name": "lago caburgua", "lat": -39.15, "lon": -71.79},
    "Calafquén": {"source": "DGA", "api_name": "lago calafquen", "lat": -39.52, "lon": -72.15},
    "Panguipulli": {"source": "DGA", "api_name": "lago panguipulli", "lat": -39.73, "lon": -72.33},
    "Riñihue": {"source": "DGA", "api_name": "lago riñihue", "lat": -39.82, "lon": -72.45},
    "Ranco": {"source": "DGA", "api_name": "lago ranco", "lat": -40.21, "lon": -72.48},
}

OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "datos.json")
CEN_USER_KEY = os.environ.get("CEN_USER_KEY", "")  # viene del secret de GitHub Actions, sin default


def normalize_name(text: str) -> str:
    if not text:
        return ""
    text = text.strip().lower()
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return " ".join(text.split())


def find_cota(data: dict, api_name: str):
    key = normalize_name(api_name)
    if key in data:
        return data[key]
    for k, v in data.items():
        if key in k or k in key:
            return v
    return None


def fetch_all_cen_cotas():
    if not CEN_USER_KEY:
        return {}, "CEN_USER_KEY no configurado (falta el secret en GitHub Actions)"
    url = "https://sipub.api.coordinador.cl/embalse-real/v3/findLast"
    headers = {"accept": "application/json"}
    params = {"user_key": CEN_USER_KEY}
    try:
        res = requests.get(url, headers=headers, params=params, timeout=15)
        if res.status_code != 200:
            msg = f"CEN HTTP {res.status_code}"
            print(f"[CEN] {msg}: {res.text[:300]}")
            return {}, msg
        data = res.json()
        out = {}
        for item in data:
            try:
                nombre = normalize_name(item.get("nombre", ""))
                cota = item.get("cotaActual")
                if nombre and cota is not None:
                    out[nombre] = float(cota)
            except (TypeError, ValueError):
                continue
        return out, None
    except requests.exceptions.RequestException as e:
        print(f"[CEN] error de red: {e}")
        return {}, "Sin conexión CEN"
    except ValueError as e:
        print(f"[CEN] respuesta no es JSON: {e}")
        return {}, "Respuesta inválida CEN"


def fetch_all_dga_cotas():
    url = "https://rest-sit.mop.gob.cl/arcgis/rest/services/DGA/ALERTAS/MapServer/0/query"
    field_nombre = "SITMOP_PROD.SITMOP_DESA.TG_RED_HIDROMETEO.NOMBRERED"
    field_valor = "SITMOP_PROD.SDE.V_DGA_GIS_ALERTAS.mod_valor"
    params = {
        "where": "1=1",
        "outFields": f"{field_nombre},{field_valor}",
        "f": "json",
        "returnGeometry": "false"
    }
    try:
        res = requests.get(url, params=params, timeout=20)
        if res.status_code != 200:
            msg = f"DGA HTTP {res.status_code}"
            print(f"[DGA] {msg}: {res.text[:300]}")
            return {}, msg
        data = res.json()
        if isinstance(data, dict) and "error" in data:
            msg = data["error"].get("message", "Error ArcGIS")
            print(f"[DGA] {msg}: {data['error']}")
            return {}, msg
        out = {}
        for feature in data.get("features", []):
            try:
                attrs = feature.get("attributes", {})
                name = normalize_name(attrs.get(field_nombre, ""))
                val = attrs.get(field_valor)
                if name and val is not None:
                    out[name] = float(val)
            except (TypeError, ValueError):
                continue
        return out, None
    except requests.exceptions.RequestException as e:
        print(f"[DGA] error de red: {e}")
        return {}, "Sin conexión DGA"
    except ValueError as e:
        print(f"[DGA] respuesta no es JSON: {e}")
        return {}, "Respuesta inválida DGA"


def fetch_weather_batch(lakes):
    """lakes: [(nombre, lat, lon), ...]. Una sola llamada para TODOS los lagos
    (ya no depende de cuáles estén 'activos' para ningún usuario en particular,
    porque este script sirve a todo el equipo a la vez)."""
    if not lakes:
        return {}
    lats = ",".join(str(l[1]) for l in lakes)
    lons = ",".join(str(l[2]) for l in lakes)
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lats,
        "longitude": lons,
        "current": "wind_speed_10m,wind_direction_10m",
        "wind_speed_unit": "kmh",
    }
    try:
        res = requests.get(url, params=params, timeout=15)
        res.raise_for_status()
        data = res.json()
        if isinstance(data, dict):
            data = [data]
        out = {}
        for (name, _, _), item in zip(lakes, data):
            cur = item.get("current", {})
            out[name] = (cur.get("wind_speed_10m"), cur.get("wind_direction_10m"))
        return out
    except Exception as e:
        print(f"[Open-Meteo] error: {e}")
        return {name: (None, None) for name, _, _ in lakes}


def main():
    cen_data, cen_err = fetch_all_cen_cotas()
    dga_data, dga_err = fetch_all_dga_cotas()
    wind_lakes = [(lk, meta["lat"], meta["lon"]) for lk, meta in LAKES_METADATA.items()]
    wind_data = fetch_weather_batch(wind_lakes)

    lakes_out = {}
    for lake, meta in LAKES_METADATA.items():
        source_data = cen_data if meta["source"] == "CEN" else dga_data
        cota = find_cota(source_data, meta["api_name"])
        spd, dir_deg = wind_data.get(lake, (None, None))
        lakes_out[lake] = {"cota": cota, "wind_speed": spd, "wind_dir": dir_deg}

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_errors": {"cen": cen_err, "dga": dga_err},
        "lakes": lakes_out,
    }

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    print(f"Escrito {OUTPUT_FILE}")
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    # Si AMBAS fuentes fallaron, marca el job como fallido en GitHub Actions
    # (se ve en rojo en el historial de runs) para que sea imposible no notarlo.
    if cen_err and dga_err:
        print("ADVERTENCIA: ambas fuentes (CEN y DGA) fallaron en esta corrida.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
