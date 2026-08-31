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
from zoneinfo import ZoneInfo

LAKES_METADATA = {
    "Rapel": {"source": "CEN", "api_name": "rapel", "lat": -34.17, "lon": -71.49},
    "Vichuquén": {"source": "DGA", "api_name": "lago vichuquen", "lat": -34.88, "lon": -72.03},
    "Colbún": {"source": "CEN", "api_name": "colbun", "lat": -35.68, "lon": -71.36},
    # Villarrica NO tiene cota DGA (nunca la tuvo). En agosto 2026 se encontró
    # que SHOA sí publica en vivo el sensor de radar de su estación en Pucón
    # (instalada en 2019 para monitoreo del volcán) a través de un endpoint
    # no documentado que usa el propio mapa público de SHOA
    # (shoa.cl/php/nivel-del-mar.php). Ver fetch_villarrica_shoa() más abajo
    # para el detalle completo y las advertencias sobre unidades/estabilidad.
    "Villarrica": {"source": "SHOA", "shoa_cod": "VILL", "lat": -39.276404, "lon": -71.981414},
    "Caburgua": {"source": "DGA", "api_name": "lago caburgua", "lat": -39.15, "lon": -71.79},
    "Calafquén": {"source": "DGA", "api_name": "lago calafquen", "lat": -39.52, "lon": -72.15},
    "Panguipulli": {"source": "DGA", "api_name": "lago panguipulli", "lat": -39.73, "lon": -72.33},
    "Riñihue": {"source": "DGA", "api_name": "lago riñihue", "lat": -39.82, "lon": -72.45},
    "Ranco": {"source": "DGA", "api_name": "lago ranco", "lat": -40.21, "lon": -72.48},
}

OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "datos.json")
HISTORIAL_RAPEL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "historial_rapel.json")
HISTORIAL_VILLARRICA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "historial_villarrica.json")
HISTORIAL_DIAS = 15  # un poco más de 2 semanas de colchón para el gráfico de la app
CEN_USER_KEY = os.environ.get("CEN_USER_KEY", "")  # viene del secret de GitHub Actions, sin default
CHILE_TZ = ZoneInfo("America/Santiago")  # los timestamps de SHOA vienen en hora local, sin offset


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


def fetch_villarrica_shoa():
    """Nivel del lago Villarrica vía la estación SHOA "VILL" (sensor de radar).

    SHOA no ofrece esto como una API pública documentada, pero su propio
    mapa web de "Nivel del Mar" (shoa.cl/php/nivel-del-mar.php) sí lo
    consulta en vivo y sin autenticación: revisando el JavaScript de esa
    página (agosto 2026, archivo mareas/js/mapa.js) se encontró este
    endpoint. Al ser no documentado, podría cambiar sin aviso -mismo tipo
    de riesgo que el endpoint roto del CEN, solo que en sentido contrario
    (por ahora funciona).

    El endpoint solo acepta ventanas cortas: "period=48" (horas) funciona
    -es lo que usa la propia página de SHOA-, pero "period=168" o superior
    fue rechazado con el error "Tiempo excede el limite de lectura". Por
    eso, en vez de pedir todo el historial de una vez, este script trae
    hasta 48h en cada corrida y actualizar_historial() se encarga de ir
    acumulando/deduplicando con lo que ya había, igual que con Rapel (pero
    acá cada corrida trae MUCHOS puntos nuevos de una vez, no solo uno,
    porque el sensor entrega una lectura por minuto).

    Sobre las unidades: el valor "DATO" es la lectura cruda del sensor de
    radar en milímetros, referida a la instalación propia de esa estación.
    NO es una cota oficial sobre el nivel del mar (msnm) como la que usa la
    DGA/CEN para el resto de los lagos -Villarrica es un lago natural, sin
    un muro de referencia como el de la hidroeléctrica de Rapel-, así que
    se usa tal cual viene, en su propia escala (dividido por 1000 para
    pasar de mm a m). Sirve perfecto para ver si el lago sube o baja y por
    cuánto, pero el número no es comparable con ningún otro "cota"
    publicado en otro lado.

    Devuelve (lista_de_(fecha_iso_utc, valor), error).
    """
    url = "https://provimar.mitelemetria.cl/apps/src/ws/wsgw.php"
    params = {
        "wsname": "getData",
        "idsensor": ";rad",
        "idestacion": "VILL",
        "period": 48,
        "fmt": "json",
        "tipo": "tecmar",
        "orden": "ASC",
    }
    try:
        res = requests.get(url, params=params, timeout=20)
        if res.status_code != 200:
            msg = f"SHOA HTTP {res.status_code}"
            print(f"[SHOA Villarrica] {msg}: {res.text[:300]}")
            return [], msg
        data = res.json()
        if not isinstance(data, list):
            msg = "Respuesta inesperada de SHOA (endpoint no documentado, puede haber cambiado)"
            print(f"[SHOA Villarrica] {msg}: {str(data)[:300]}")
            return [], msg
        puntos = []
        for item in data:
            try:
                if item.get("SENSOR") != "RAD":
                    continue
                dato = item.get("DATO")
                if dato is None:
                    continue
                dt_local = datetime.strptime(item["FECHA"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=CHILE_TZ)
                dt_utc = dt_local.astimezone(timezone.utc)
                puntos.append((dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), round(float(dato) / 1000, 3)))
            except (KeyError, TypeError, ValueError):
                continue
        return puntos, None
    except requests.exceptions.RequestException as e:
        print(f"[SHOA Villarrica] error de red: {e}")
        return [], "Sin conexión SHOA"
    except ValueError as e:
        print(f"[SHOA Villarrica] respuesta no es JSON: {e}")
        return [], "Respuesta inválida SHOA"


def actualizar_historial(lago, archivo, puntos_nuevos):
    """Construye nuestro propio historial de cota/nivel, corrida a corrida.
    Función común para Rapel y Villarrica (generalizada en agosto 2026 al
    agregar Villarrica; antes era "actualizar_historial_rapel", específica).

    Por qué existe: la API del CEN para traer un RANGO de fechas
    (/cotas-embalses-reales/v3/findAll) está rota en su servidor (devuelve
    "Internal server error" incluso con las fechas de ejemplo de su propia
    documentación oficial - probado en agosto 2026), y el endpoint de SHOA
    que usamos para Villarrica solo acepta ventanas cortas (ver
    fetch_villarrica_shoa). En vez de depender de un historial completo en
    una sola consulta, lo vamos construyendo nosotros: cada corrida de este
    script agrega los puntos nuevos al archivo docs/historial_<lago>.json,
    que git-auto-commit-action persiste en el repo. Así el archivo YA trae
    acumulado lo de corridas anteriores cuando este script arranca (git
    checkout lo trae fresco).

    puntos_nuevos: lista de tuplas (fecha_iso_utc, valor) a agregar. Puede
    ser un solo punto (caso Rapel: CEN solo da el último valor) o muchos de
    una vez (caso Villarrica: SHOA entrega hasta 48h por consulta). Se
    deduplica por fecha exacta, así que no importa que una corrida traiga
    puntos que ya estaban.
    """
    historial = []
    if os.path.exists(archivo):
        try:
            with open(archivo, "r", encoding="utf-8") as f:
                historial = json.load(f).get("historial", [])
        except Exception as e:
            print(f"[historial] no se pudo leer {archivo}, se reinicia: {e}")
            historial = []

    fechas_existentes = {e.get("fecha") for e in historial}
    for fecha, valor in puntos_nuevos:
        if fecha not in fechas_existentes:
            historial.append({"fecha": fecha, "cota": valor})
            fechas_existentes.add(fecha)

    historial.sort(key=lambda e: e.get("fecha", ""))

    # Poda: se descarta todo lo más viejo que HISTORIAL_DIAS, para que el
    # archivo no crezca sin límite.
    corte = datetime.now(timezone.utc).timestamp() - HISTORIAL_DIAS * 86400

    def es_reciente(entry):
        try:
            dt = datetime.strptime(entry["fecha"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            return dt.timestamp() >= corte
        except (KeyError, ValueError, TypeError):
            return False

    historial = [e for e in historial if es_reciente(e)]

    with open(archivo, "w", encoding="utf-8") as f:
        json.dump({"lago": lago, "historial": historial}, f, indent=2, ensure_ascii=False)

    print(f"Escrito {archivo} ({len(historial)} puntos)")


def main():
    cen_data, cen_err = fetch_all_cen_cotas()
    dga_data, dga_err = fetch_all_dga_cotas()
    villarrica_puntos, shoa_err = fetch_villarrica_shoa()
    wind_lakes = [(lk, meta["lat"], meta["lon"]) for lk, meta in LAKES_METADATA.items()]
    wind_data = fetch_weather_batch(wind_lakes)

    lakes_out = {}
    for lake, meta in LAKES_METADATA.items():
        if meta["source"] == "SHOA":
            # Villarrica no viene de CEN ni DGA: se completa aparte, más
            # abajo, con el último punto que trajo fetch_villarrica_shoa().
            cota = None
        else:
            source_data = cen_data if meta["source"] == "CEN" else dga_data
            cota = find_cota(source_data, meta["api_name"])
        spd, dir_deg = wind_data.get(lake, (None, None))
        lakes_out[lake] = {"cota": cota, "wind_speed": spd, "wind_dir": dir_deg}

    if villarrica_puntos:
        lakes_out["Villarrica"]["cota"] = villarrica_puntos[-1][1]

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_errors": {"cen": cen_err, "dga": dga_err, "shoa_villarrica": shoa_err},
        "lakes": lakes_out,
    }

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    print(f"Escrito {OUTPUT_FILE}")
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    cota_rapel = lakes_out.get("Rapel", {}).get("cota")
    actualizar_historial(
        "Rapel", HISTORIAL_RAPEL_FILE,
        [(payload["generated_at"], cota_rapel)] if cota_rapel is not None else []
    )
    actualizar_historial("Villarrica", HISTORIAL_VILLARRICA_FILE, villarrica_puntos)

    # Si AMBAS fuentes fallaron, marca el job como fallido en GitHub Actions
    # (se ve en rojo en el historial de runs) para que sea imposible no notarlo.
    # (SHOA/Villarrica queda afuera de esta condición a propósito: es un
    # extra, no una de las dos fuentes principales del resto de los lagos.)
    if cen_err and dga_err:
        print("ADVERTENCIA: ambas fuentes (CEN y DGA) fallaron en esta corrida.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
