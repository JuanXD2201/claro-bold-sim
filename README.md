# Simulador de orquestador de pools de datos (Claro / Bold)

Aplicación web **local** para simular cómo un orquestador reparte 60 SIMs de datáfonos en pools de datos.
Todo es sintético y determinista (semilla fija); **no se conecta a ningún sistema real**. Escucha solo en `127.0.0.1`.

Guía con capturas de cada sección: [docs/GUIA.md](docs/GUIA.md). Informe de pruebas en Word con capturas: [docs/Informe_pruebas.docx](docs/Informe_pruebas.docx).

## Instalación y arranque

Requiere Python 3.10+ (probado con 3.12). Dependencias: `fastapi`, `uvicorn` (+ `httpx` solo para las pruebas).

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt     # Windows  (Linux/macOS: .venv/bin/python)
.venv\Scripts\python -m orq                                  # un solo comando → http://127.0.0.1:8000
```

- UI: <http://127.0.0.1:8000/> · Swagger/OpenAPI: <http://127.0.0.1:8000/docs>
- Estado persistente en `data/sim.db` (SQLite). «Reiniciar todo» en la UI vuelve a la semilla inicial.

## Versión publicada (GitHub Pages)

**https://juanxd2201.github.io/claro-bold-sim/** — se abre desde cualquier navegador, sin instalar ni arrancar nada.

El simulador Python corre dentro del navegador (Pyodide/WebAssembly): la UI detecta que no hay servidor y lo inicia sola.
La primera carga descarga el motor Python (~10 MB, unos segundos); después queda en caché. Cada visitante tiene su propia
simulación en memoria (se reinicia al recargar la página). Requiere internet para bajar Pyodide y `fastapi` (CDN/PyPI).

Para actualizar la página tras cambiar el código:

```bash
python scripts/build_site.py                 # genera site/
cd site && git init -q -b gh-pages && git add -A && git commit -qm "Publicar sitio" \
  && git push -f https://github.com/JuanXD2201/claro-bold-sim.git gh-pages
```

## Pruebas

Resultados de las pruebas hechas (automáticas y manuales): [docs/PRUEBAS.md](docs/PRUEBAS.md).

```bash
.venv\Scripts\python -m unittest discover -s tests -t .     # núcleo + API + e2e HTTP
```

- `tests/test_core.py`: semilla, reloj, plataforma (idempotencia, 429, fallas), motor, conciliación, R4, reportes, escenarios.
- `tests/test_api.py`: API vía `TestClient` (códigos de error, bulk parcial, límite de tasa, CORS/Host, reporte CSV = suma de pools).
- `tests/test_e2e.py`: levanta el servidor real y recorre mover SIM, filtrar y aprobar un movimiento por HTTP.
- E2E con navegador (opcional, no incluido por peso): `pip install playwright && playwright install chromium` y apuntar a `http://127.0.0.1:8000`.

## Modelo simulado

3 pools operativos de 20 SIMs (capacidad 800 MB/periodo de 30 días) + `POOL-OVF` (capacidad 0: todo su uso es excedente).
Consumo por perfil (MB/día): bajo 0,3 · medio 1,0 · alto 3,0, con ruido diario ±20 % reproducible.
A (12 altos, 6 medios, 2 bajos) se satura; B (4/8/8) queda cerca del umbral; C (20 bajos) tiene holgura.

**Reglas** (`/orchestrator/rules`): `warn_threshold` (0,8), `critical_threshold` (1,0), `mode` (supervisado/automático),
`trigger` (real/proyectado), `min_history_days` (5), `max_moves_per_cycle`, `cooldown_days`.
El orquestador propone mover SIMs cuando el uso (real o proyectado) de un pool supera el umbral de aviso **y** la proyección al día 30
excede capacidad × umbral crítico. Elige la SIM más pequeña que basta y el pool con más holgura; si ninguno cabe, va a `POOL-OVF`.
**R4**: al cerrar el periodo las SIMs en desborde vuelven a su pool de origen.

**Movimientos**: `proposed → approved → executed | failed`, además de `rejected` y `reverted`. Ejecución con reintentos (500/503, hasta 3,
misma `Idempotency-Key`), diferido ante 429, y **conciliación** en cada ciclo (la plataforma es la verdad: cura movimientos aplicados
cuyo éxito no se vio y adopta cambios externos, con alerta `conciliacion`).

## Endpoints principales (detalle en `/docs`)

Errores siempre con cuerpo `{"error": {"code","message","status","details"}}` (400, 404, 409, 422, 429 con `Retry-After`, 500, 503).

| Prefijo | Endpoints |
|---|---|
| `/platform` | `GET /pools`, `GET /sims` (filtros, orden, paginación), `GET /sims/{iccid}`, `GET /usage`, `POST /sims/{iccid}/move` (cabecera `Idempotency-Key`), `POST /sims/bulk-move` (resultado por elemento), `POST /sims/{iccid}/suspend`, `/activate` |
| `/orchestrator` | `rules` (GET/PUT, `POST /rules/preview`), `moves` (listar, manual, bulk, approve/reject/execute/retry/revert, por lote), `alerts` (listar, acknowledge, resolve), `audit` y `audit.csv`, `reports/monthly` (JSON o `?format=csv`), `cycle`, `summary`, `pools`, `series` |
| `/sim` | control local: `play`, `pause`, `speed`, `tick`, `advance-to-period-end`, `close-period`, `reset`, `scenarios/{key}`, `latency`, `rate-limit`, `faults`, `spike`, `api-log`, `events` (SSE) |

Las lecturas de la UI (`X-Client: ui`, método GET) no consumen el límite de tasa, no sufren fallas inyectadas y no ensucian el registro;
todo lo demás sí cuenta (probador, orquestador y llamadas externas).

## Escenarios (botón «Escenarios»)

| Escenario | Qué deja listo |
|---|---|
| Normal | Operación base, modo supervisado, sin fallas |
| Pool A se satura | Día 4: alertas de umbral y 7 propuestas pendientes de aprobar |
| Fallas de API | Automático; 2 fallas 503 transitorias (reintento exitoso) + falla permanente en la SIM más consumidora de A (movimiento fallido, alertas) |
| Límite de tasa | Automático con 3 llamadas por ciclo: 429, movimientos diferidos y alerta |
| Pico anómalo en una SIM | SIM de C ×15 durante 2 días: alerta de anomalía |
| Desborde y cierre de periodo | Automático hasta el día 30; pulsa «Reiniciar periodo» para ver el retorno desde `POOL-OVF` (R4) |

## Interfaz

Vistas: Resumen, Pools (arrastrar y soltar), SIMs (filtros en la URL, acciones masivas, CSV), Movimientos, Alertas, Reglas (vista previa),
Consola API (registro en vivo, probador, latencia/límite/fallas) y Auditoría. Tema claro/oscuro, atajos (`?` los lista), actualización
en vivo por SSE con sondeo de respaldo. Sin build: módulos ES y SVG propio.

## Rendimiento

Cada ciclo diario del motor se mide (`GET /orchestrator/cycle`, tabla en la consola): ~20 ms con 60 SIMs en el equipo de desarrollo.
La UI agrupa eventos (≈ 3 refrescos/s) para fluir a 20x.

## Seguridad

Solo `127.0.0.1`, `TrustedHost` (anti DNS-rebinding), CORS limitado a `127.0.0.1:8000`/`localhost:8000`, cuerpos con esquemas estrictos
(`extra=forbid`), renderizado DOM sin `innerHTML`, sin secretos ni datos reales (ICCID/MSISDN/comercios ficticios).
