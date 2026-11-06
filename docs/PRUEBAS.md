# Pruebas realizadas

Fecha: 2026-11-05 · Python 3.12 · Chrome · Windows 10.

## 1. Pruebas automáticas

Comando: `.venv\Scripts\python -m unittest discover -s tests -t .`
Resultado: **68 pruebas, todas OK**.

| Archivo | Qué cubre |
|---|---|
| `tests/test_core.py` | Datos de semilla, reloj, idempotencia, 429, fallas, reintentos, conciliación, retorno desde desborde (R4), reporte, escenarios |
| `tests/test_api.py` | Códigos de error (400/404/409/422/429/500/503), `bulk-move` parcial, límite de tasa, reglas, alertas, CSV, CORS y Host |
| `tests/test_e2e.py` | Servidor real: mover una SIM, filtrar y aprobar un movimiento |

## 2. Pruebas manuales en el navegador

Servidor: `python -m orq` → http://127.0.0.1:8000. Cada prueba se hizo en la UI real.

| # | Prueba | Resultado |
|---|---|---|
| 1 | Las 8 vistas cargan sin errores de JavaScript | OK |
| 2 | Filtros: pool+perfil (12 SIMs), búsqueda "Panadería" (2), + ciudad (1), limpiar (60), orden, anomalía sí/no. Se guardan en la URL | OK |
| 3 | Acciones masivas: suspender, activar, mover varias SIMs, exportar CSV | OK |
| 4 | Arrastrar una SIM de POOL-A a POOL-C: pide confirmación, API 200 con latencia, queda en el registro | OK |
| 5 | Aprobar un movimiento propuesto (modo supervisado) | OK, queda `executed` |
| 6 | Escenario "Fallas de API": reintento exitoso (alerta info), movimiento fallido (alerta crítica), reintentar tras quitar la falla | OK |
| 7 | Escenario "Límite de tasa": alerta y movimiento diferido en `approved` | OK |
| 8 | Consola: mover con `Idempotency-Key` dos veces (la 2ª marca `Idempotent-Replay`), sin clave → 400, 429 con `Retry-After: 7` | OK |
| 9 | Reglas: la vista previa cambia al editar, valor inválido muestra error, guardar cambia el modo a automático | OK |
| 10 | Escenario "Desborde y cierre": al cerrar el periodo la SIM vuelve de POOL-OVF a su pool (R4) | OK |
| 11 | Reporte mensual y auditoría CSV: total = suma de pools (2126,221 MB) y la auditoría incluye `period.closed` | OK |
| 12 | Teclado: `3` cambia de vista, `?` abre ayuda, `n` avanza un día | OK |
| 13 | Reproducir a 20x durante 6 s sin errores; ciclo del motor ~18 ms promedio, 24 ms máximo | OK |
| 14 | Móvil (390 px, emulado con iframe) sin scroll horizontal en las 8 vistas | OK tras corregir la Consola (ver abajo) |
| 15 | Cambio de tema claro/oscuro | El botón cambia el atributo `data-theme`; no revisé el contraste visualmente |

## 3. Fallos encontrados y corregidos

- **Consola API desbordaba en móvil** (750 px de ancho en pantalla de 390): se agregó `min-width:0` a la cuadrícula en `web/css/app.css`.
- **Mensaje en inglés** "Falla permanent inyectada": ahora dice "transitoria/permanente" (`orq/platform_mock.py`).
- Error de sintaxis en `web/js/views/audit.js` (un paréntesis de más), detectado al abrir la UI por primera vez.

## 4. No probado

- Contraste de colores y lector de pantalla (solo se revisó el teclado básico).
- Navegadores distintos de Chrome.
- Pantalla móvil real (se emuló con un iframe, no con el dispositivo).
- Tests e2e con Playwright (no está instalado; los e2e incluidos son por HTTP).
