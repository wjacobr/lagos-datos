# lagos-datos

Publicador automático de datos para la app "Alertas Lagos". No es la app en
sí — es un pequeño robot que cada 30 minutos consulta CEN, DGA y Open-Meteo
UNA vez, y deja el resultado en `docs/datos.json`, accesible como una URL
pública fija vía GitHub Pages. La app Flet solo lee esa URL: nunca vuelve a
llamar a CEN/DGA/Open-Meteo directamente, así que el límite de 60
consultas/hora del CEN deja de ser un problema sin importar cuánta gente use
la app.

## Puesta en marcha (una sola vez)

1. **Crear el repositorio en GitHub, como PÚBLICO.**
   Entra a github.com → "New repository". Nómbralo como quieras (ej.
   `lagos-datos`) y marca **Public**.

   Nota sobre por qué público: con plan GitHub Free, un repo **privado no
   puede publicar GitHub Pages en absoluto** (el paso 4 de abajo fallaría).
   Y aunque tuvieras GitHub Pro (que sí permite Pages desde un repo privado),
   el sitio publicado **igual queda accesible a cualquiera con la URL** — la
   privacidad del repo y la privacidad del sitio publicado son cosas
   separadas, y un sitio de Pages realmente restringido solo existe en
   GitHub Enterprise Cloud. Como el `datos.json` va a ser público de todas
   formas, no perdemos nada haciendo también público el repo (el código y la
   lista de lagos/umbrales por defecto) — y de paso, Actions corre gratis sin
   límite práctico de minutos en repos públicos.
   El único dato realmente sensible, el `CEN_USER_KEY`, NUNCA queda expuesto
   sin importar la visibilidad del repo: vive solo como secret de Actions
   (paso 3), que nadie puede leer ni siendo colaborador del repo.

2. **Subir estos archivos al repo.**
   Desde esta carpeta:
   ```bash
   git init
   git add .
   git commit -m "Setup inicial del publicador de datos"
   git branch -M main
   git remote add origin https://github.com/TU-USUARIO/TU-REPO.git
   git push -u origin main
   ```

3. **Cargar tu API key del CEN como secret (nunca como texto en el código).**
   En GitHub: Settings del repo → "Secrets and variables" → "Actions" →
   "New repository secret".
   - Nombre: `CEN_USER_KEY`
   - Valor: el user_key que generes en https://sipub.coordinador.cl/
     (ver conversación anterior sobre cómo registrarte ahí).

   El script lee esta variable en tiempo de ejecución del workflow — nunca
   queda escrita en el código ni en el historial de git.

4. **Habilitar GitHub Pages.**
   Settings del repo → "Pages" → "Build and deployment" → Source: "Deploy
   from a branch" → Branch: `main`, carpeta `/docs` → Save.
   GitHub te va a dar una URL del estilo:
   ```
   https://TU-USUARIO.github.io/TU-REPO/
   ```
   El JSON va a quedar servido en:
   ```
   https://TU-USUARIO.github.io/TU-REPO/datos.json
   ```
   (puede tardar 1-2 minutos en activarse la primera vez).

5. **Correr el workflow manualmente la primera vez.**
   Pestaña "Actions" del repo → "Actualizar datos de lagos" → "Run
   workflow". Esto genera el primer `datos.json` real (el que subiste de
   ejemplo tiene todo en `null`). Después de eso corre solo cada 30 minutos.

6. **Verificar que funciona.**
   Abre en el navegador `https://TU-USUARIO.github.io/TU-REPO/datos.json` y
   confirma que `generated_at` tiene una fecha reciente y que al menos
   algunos lagos tienen `cota` distinto de `null`. Si `source_errors.cen` o
   `source_errors.dga` no son `null`, ahí te va a decir exactamente qué
   falló (por ejemplo, un 403 si el CEN_USER_KEY todavía no es válido).

7. **Apuntar la app a esta URL.**
   En `main.py` de la app, reemplaza la constante `DATA_URL` por tu URL real
   de GitHub Pages (paso 4).

## Ajustar la frecuencia

El cron está en `.github/workflows/actualizar-datos.yml`. Por defecto corre
cada 30 minutos (`*/30 * * * *`). Con el repo público no hay límite práctico
de minutos de Actions, así que no hace falta tocar esto — pero si algún día
lo pasas a privado, cambia a cada hora (`0 * * * *`) para no acercarte al
cupo mensual gratis.

## Historial de Rapel (`docs/historial_rapel.json`)

La app ahora muestra un gráfico de las últimas ~2 semanas de cota de Rapel.
El endpoint oficial del CEN para pedir un RANGO de fechas
(`/cotas-embalses-reales/v3/findAll`) está roto en su servidor — devuelve
"Internal server error" incluso con las fechas de ejemplo de su propia
documentación (probado en agosto 2026) — así que en vez de depender de eso,
`fetch_and_publish.py` arma su propio historial: cada corrida agrega un
punto nuevo (fecha + cota de Rapel) a `docs/historial_rapel.json` y descarta
lo más viejo que 15 días. El workflow ya está configurado para commitear
también este archivo (ver `file_pattern` en
`.github/workflows/actualizar-datos.yml`).

Esto significa que el gráfico arranca vacío ("Acumulando historial...") y se
va llenando solo con el correr de los días — no hace falta ninguna acción
manual, pero toma unas horas/días en tener una curva completa. Si algún día
CEN arregla su endpoint de rango, se puede reemplazar esta lógica por una
consulta directa.

## Villarrica: fuente SHOA (`docs/historial_villarrica.json`)

Villarrica nunca tuvo cota DGA (fue el motivo original por el que se
descartó como fuente confiable). En agosto 2026 se encontró que SHOA sí
publica en vivo el sensor de radar de su estación en Pucón (instalada en
2019 para monitoreo del volcán) a través de un endpoint no documentado que
usa el propio mapa público de SHOA (`shoa.cl/php/nivel-del-mar.php` →
revisando su JavaScript se encontró la consulta real a
`provimar.mitelemetria.cl`). Ver el docstring de `fetch_villarrica_shoa()`
en `fetch_and_publish.py` para el detalle completo.

Dos cosas importantes a tener en cuenta:

- **No es una cota oficial en msnm.** El valor es la lectura cruda del
  sensor de radar, en la escala propia de esa instalación (a diferencia de
  Rapel, que tiene el muro de la hidroeléctrica como referencia fija,
  Villarrica es un lago natural sin una referencia de ese tipo). Sirve
  perfecto para ver si el lago sube o baja y por cuánto, pero el número no
  se puede comparar con ninguna cota publicada en otro lado. El umbral de
  alerta de Villarrica en la app se configura en esa misma escala propia.
- **Es un endpoint no documentado**, encontrado leyendo el JavaScript del
  sitio de SHOA, no una API pública con contrato formal — podría cambiar
  sin aviso, igual que le pasó al endpoint de rango del CEN (con la
  diferencia de que este, por ahora, funciona).

A diferencia de Rapel (que solo agrega un punto por corrida, porque CEN
únicamente entrega el último valor), el endpoint de SHOA sí entrega un
rango — hasta 48 horas por consulta (ventanas más largas son rechazadas con
"Tiempo excede el limite de lectura") — así que cada corrida trae muchos
puntos nuevos de una vez y `actualizar_historial()` los deduplica por fecha
contra lo que ya había en `docs/historial_villarrica.json`. En la práctica,
el historial de Villarrica se llena mucho más rápido que el de Rapel.

## Cuando agreguen o quiten un lago

`LAKES_METADATA` en `fetch_and_publish.py` debe tener las mismas llaves
(nombres de lago) que `LAKES_METADATA` en el `main.py` de la app. Si agregas
un lago nuevo, agrégalo en ambos archivos.
