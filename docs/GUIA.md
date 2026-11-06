# Guía de uso con capturas

Simulador de un orquestador que reparte 60 SIMs de datáfonos en pools de datos. Todo es sintético.
Prueba en línea: https://juanxd2201.github.io/claro-bold-sim/ (no hay que instalar nada).

Las capturas están en modo oscuro; hay modo claro con el botón **Tema**.

## Barra superior (siempre visible)

Reloj simulado (`Periodo 1 · Día 4/30`), **Reproducir/Pausar**, velocidad **1x / 5x / 20x**, **+1 día**, **A fin de periodo**,
**Reiniciar periodo** (cierra el periodo), **Reiniciar todo** (vuelve a la semilla inicial) y **Escenarios**.
A la derecha: modo del orquestador (supervisado/automático), campana de **Alertas** con contador y **Tema**.

### Escenarios
Cada botón reinicia la simulación y la deja lista para probar un caso.

![Escenarios](img/10-escenarios.jpg)

## 1. Resumen
Tarjetas (SIMs activas, consumo, excedente, movimientos pendientes, alertas), gráfico de uso por pool contra las líneas de
umbral (80 %) y capacidad (100 %) con la proyección al día 30, y tabla de proyección por pool.

![Resumen](img/01-resumen.jpg)

**Reporte mensual:** abajo del Resumen. Se elige el periodo y se exporta a CSV o JSON. El total siempre es la suma de los pools
(la etiqueta verde "Total = Σ pools ✓" lo confirma). Aquí el periodo 1 cerrado: 2.126,2 MB en total, 81,3 MB de excedente en el desborde.

![Reporte mensual](img/19-reporte-periodo-1.jpg)

## 2. Pools
Una tarjeta por pool con barra de uso, marcador de proyección, SIMs y top de consumidoras. Para mover una SIM, **arrástrala a otro pool**
(pide confirmación y muestra el resultado de la API). Abre "SIMs (n)" para ver las SIMs de cada pool; el color del borde indica el perfil.

![Pools](img/02-pools.jpg)

**Con desborde:** al final del periodo, las SIMs que no cupieron en ningún pool quedan en `POOL-OVF` y todo su consumo es excedente.

![Pools con desborde](img/17-pools-con-desborde.jpg)

**Tras cerrar el periodo:** el consumo vuelve a cero y la SIM en desborde regresa a su pool de origen (regla R4).

![Pools tras cierre](img/18-pools-tras-cierre.jpg)

## 3. SIMs
Búsqueda por ICCID, MSISDN, comercio o datáfono; filtros combinables (pool, estado, perfil, ciudad, anomalía, movimiento pendiente,
movida recientemente, rangos de consumo). Los filtros quedan en la URL. Se ordena por columna, se seleccionan varias SIMs y aparecen
las acciones masivas: **Mover…, Suspender, Activar, Exportar CSV**.

![SIMs](img/03-sims.jpg)

### Mover una SIM
Se elige el pool destino. El diálogo muestra la respuesta de la API (código HTTP, latencia y cuerpo).

![Mover SIM](img/11-mover-sim.jpg)

### Detalle de una SIM
Al hacer clic en un ICCID se abre el panel lateral: datos, gráfico de consumo diario y acumulado, movimientos, alertas y línea de
tiempo de auditoría. Desde ahí se puede mover, revertir el último movimiento o suspender.

![Detalle de SIM](img/04-detalle-sim.jpg)

## 4. Movimientos
Cola con pestañas por estado (propuesto, aprobado, ejecutado, fallido, rechazado, revertido). En modo supervisado el orquestador solo
**propone**; tú **apruebas o rechazas** (uno o varios). Los fallidos se **reintentan** y los ejecutados se **revierten** (también por lote).

![Movimientos](img/05-movimientos.jpg)

Un movimiento **fallido** muestra el error y cuántos intentos se hicieron (aquí 3 por una falla permanente inyectada):

![Movimiento fallido](img/13-movimiento-fallido.jpg)

## 5. Alertas
Severidad, tipo (umbral, agotado, anomalía, falla de API, límite de tasa, conciliación) y estado. Se **reconocen** y **resuelven**;
el enlace lleva a la SIM o al pool.

![Alertas](img/06-alertas.jpg)

**Fallas de API:** una crítica (movimiento fallido tras 3 intentos) y una informativa (se recuperó tras reintentar).

![Alertas por fallas de API](img/12-alertas-fallas-api.jpg)

**Límite de tasa:** la plataforma respondió 429 y los movimientos quedaron diferidos.

![Alerta de límite de tasa](img/15-alerta-limite-tasa.jpg)

**Anomalía:** una SIM de bajo consumo (0,3 MB/día) llegó a 4,07 MB/día.

![Alerta de anomalía](img/16-alerta-anomalia.jpg)

## 6. Reglas
Umbral de aviso y crítico, modo (supervisado/automático), disparador (real o proyectado), días mínimos de historial y límites.
A la derecha, una **vista previa** de los movimientos que se propondrían con los valores actuales, antes de guardar.

![Reglas](img/07-reglas.jpg)

## 7. Consola API
Registro en vivo de las llamadas (origen, método, ruta, estado y latencia; "ver" muestra petición y respuesta), **probador de endpoints**
y condiciones de la API simulada. En la captura se envió dos veces el mismo movimiento con la misma `Idempotency-Key`: la segunda marca `Idempotent-Replay` y no mueve la SIM otra vez.

![Consola API](img/08-consola-api.jpg)

Más abajo: latencia, límite de llamadas por ciclo, inyección de fallas (transitorias/permanentes, globales o por SIM) y la medición de cada ciclo del motor.

![Condiciones de la API](img/14-consola-condiciones.jpg)

## 8. Auditoría
Todo lo que pasa queda registrado (actor, evento, entidad, detalle). Se filtra por actor, evento, entidad y fecha, y se exporta a CSV.

![Auditoría](img/09-auditoria.jpg)

## Otros
**Modo claro:**

![Modo claro](img/20-tema-claro.jpg)

**Móvil (390 px):** la barra se acomoda en varias filas y las vistas no tienen scroll horizontal.

![Móvil](img/21-movil.jpg)

**Atajos de teclado:** `Espacio` reproduce/pausa, `n` avanza un día, `1`–`8` cambia de vista, `/` busca SIM, `?` muestra la ayuda.
