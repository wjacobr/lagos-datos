import requests
import json

def explorar_api_dga():
    # URL del servicio ArcGIS de la DGA para alertas en línea
    url = "https://rest-sit.mop.gob.cl/arcgis/rest/services/DGA/ALERTAS/MapServer/0/query"
    
    # Parámetros para descargar en formato JSON y traer todos los campos (*)
    params = {
        "where": "1=1",         # Traer todo
        "outFields": "*",       # Extraer todas las columnas/variables
        "f": "json",            # Formato de respuesta
        "returnGeometry": "false" # No necesitamos las coordenadas poligonales ahora
    }

    try:
        print("Conectando con el servidor ArcGIS de la DGA...")
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        features = data.get("features", [])
        print(f"✅ Conexión exitosa. Se encontraron {len(features)} estaciones transmitiendo.\n")

        # Lista de los lagos que queremos filtrar
        lagos_objetivo = ["villarrica", "caburgua", "calafquen", "panguipulli", "riñihue", "ranco", "vichuquen"]

        print("-" * 50)
        for feature in features:
            atributos = feature.get("attributes", {})
            
            # El nombre de la estación suele venir en el campo 'Nombre_Estacion' o similar
            # Buscaremos en todos los valores de texto del diccionario
            valores_texto = " ".join([str(v).lower() for v in atributos.values() if isinstance(v, str)])
            
            if any(lago in valores_texto for lago in lagos_objetivo):
                print("¡Lago Encontrado!")
                print(json.dumps(atributos, indent=2, ensure_ascii=False))
                print("-" * 50)

    except Exception as e:
        print(f"Error conectando a la API de la DGA: {e}")

explorar_api_dga()