"""Puente para ejecutar el simulador dentro del navegador (Pyodide), sin servidor.

El JS llama a `handle(...)` por cada petición HTTP simulada y a `tick_if_due()` desde un temporizador.
Se reutiliza la misma app FastAPI (ASGI) invocada directamente: no hay sockets ni hilos.
"""
import json
import os

os.environ.setdefault("ORQ_FAKE_LATENCY", "1")

import anyio.to_thread  # noqa: E402


async def _inline(func, *args, **kwargs):      # WebAssembly no tiene hilos: los endpoints síncronos corren en línea
    return func(*args)

anyio.to_thread.run_sync = _inline

from .api.app import create_app  # noqa: E402
from .sim import Simulation  # noqa: E402

sim = Simulation(":memory:")
app = create_app(sim, run_clock=False, serve_static=False)


async def handle(method, path, query, headers_json, body):
    headers = [(k.lower().encode(), str(v).encode()) for k, v in json.loads(headers_json).items()]
    headers.append((b"host", b"127.0.0.1"))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "http",
             "path": path, "raw_path": path.encode(), "query_string": query.encode(), "headers": headers,
             "client": ("127.0.0.1", 0), "server": ("127.0.0.1", 80), "root_path": ""}
    payload = (body or "").encode()
    sent, start, chunks = False, {}, []

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            start.update(status=msg["status"], headers=[[k.decode(), v.decode("latin-1")] for k, v in msg.get("headers", [])])
        elif msg["type"] == "http.response.body":
            chunks.append(msg.get("body", b""))

    await app(scope, receive, send)
    return json.dumps({"status": start.get("status", 500), "headers": start.get("headers", []),
                       "body": b"".join(chunks).decode("utf-8", "replace")})


def tick_if_due():
    """Avanza el reloj simulado si toca (equivale al bucle del servidor). Devuelve True si hubo cambios."""
    if not sim.due():
        return False
    try:
        sim.tick_day()
    except Exception:
        sim.pause()
    return True
