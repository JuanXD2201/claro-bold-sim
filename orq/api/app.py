"""Aplicación FastAPI: rutas, errores uniformes, log de llamadas, CORS restringido, bucle del reloj y SPA estática."""
import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..platform_mock import PlatformError
from ..sim import Simulation
from . import routes_orch, routes_platform, routes_sim

log = logging.getLogger("orq")
WEB_DIR = Path(__file__).resolve().parents[2] / "web"
LOGGED_PREFIXES = ("/platform", "/orchestrator")
# Local por defecto. Para desplegar: ORQ_HOST=0.0.0.0, PORT=<puerto>, ORQ_ALLOWED_HOSTS=*.onrender.com
HOST = os.environ.get("ORQ_HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))
ALLOWED_ORIGINS = [f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"]
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"] + [h for h in os.environ.get("ORQ_ALLOWED_HOSTS", "").split(",") if h]


def _err(status, code, message, details=None, headers=None):
    return JSONResponse({"error": {"code": code, "message": message, "status": status, "details": details}},
                        status_code=status, headers=headers)


def _short(raw, limit=1500):
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except ValueError:
        return raw[:limit].decode("utf-8", "replace")
    text = json.dumps(obj, ensure_ascii=False)
    return obj if len(text) <= limit else text[:limit] + "…"


class ApiLogMiddleware:
    """Registra las llamadas a /platform y /orchestrator (omite lecturas de la UI) y notifica cambios."""

    def __init__(self, app, sim_getter):
        self.app, self.sim_getter = app, sim_getter

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or not path.startswith(LOGGED_PREFIXES):
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode("latin-1") for k, v in scope["headers"]}
        client, method = headers.get("x-client", "external")[:20], scope["method"]
        req, resp, status = [], [], [500]

        async def recv():
            m = await receive()
            if m["type"] == "http.request":
                req.append(m.get("body", b""))
            return m

        async def snd(m):
            if m["type"] == "http.response.start":
                status[0] = m["status"]
                status.append({k.decode().lower(): v.decode("latin-1") for k, v in m.get("headers", [])})
            elif m["type"] == "http.response.body":
                resp.append(m.get("body", b""))
            await send(m)

        t0 = time.perf_counter()
        await self.app(scope, recv, snd)
        sim = self.sim_getter()
        if client == "ui" and (method == "GET" or path.endswith("/rules/preview")):   # lecturas de la UI: no se registran
            return
        qs = scope.get("query_string", b"").decode()
        sim.apilog.add(source=client, method=method, path=path + (f"?{qs}" if qs else ""), status=status[0],
                       latency_ms=round((time.perf_counter() - t0) * 1000 + scope.get("state", {}).get("fake_latency_ms", 0), 1),
                       request=_short(b"".join(req)), response=_short(b"".join(resp)),
                       retry_after=(status[1].get("retry-after") if len(status) > 1 else None))
        if method != "GET":
            sim.bump()


def create_app(sim=None, db_path=None, run_clock=True, serve_static=True):
    holder = {}

    @asynccontextmanager
    async def lifespan(app):
        default_db = Path(__file__).resolve().parents[2] / "data" / "sim.db"
        default_db.parent.mkdir(exist_ok=True)
        app.state.sim = holder["sim"] = sim or Simulation(db_path or str(default_db))
        task = asyncio.create_task(_clock_loop(app.state.sim)) if run_clock else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="Simulador de orquestador de pools (Claro/Bold) — datos sintéticos", version="1.0.0",
                  description="API local. Todo es simulado: sin conexión a sistemas reales. "
                              "Errores con cuerpo uniforme `{error:{code,message,status,details}}`.",
                  lifespan=lifespan)
    if sim:                       # pruebas: la instancia ya existe antes de arrancar el lifespan
        app.state.sim = holder["sim"] = sim
    app.add_middleware(ApiLogMiddleware, sim_getter=lambda: holder["sim"])
    app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_methods=["GET", "POST", "PUT", "DELETE"],
                       allow_headers=["Content-Type", "Idempotency-Key", "X-Client"],
                       expose_headers=["Retry-After", "Idempotent-Replay"])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)

    @app.exception_handler(PlatformError)
    async def _platform_error(_, e: PlatformError):
        h = {"Retry-After": str(e.retry_after)} if e.retry_after else None
        return _err(e.status, e.code, e.message, e.details, h)

    @app.exception_handler(RequestValidationError)
    async def _validation(_, e: RequestValidationError):
        details = [{"loc": list(x["loc"]), "msg": x["msg"], "type": x["type"]} for x in e.errors()]
        return _err(422, "validation_error", "Entrada inválida", details)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_, e: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(e.status_code, "http_error")
        return _err(e.status_code, code, str(e.detail))

    @app.exception_handler(Exception)
    async def _unhandled(_, e: Exception):
        log.exception("error no controlado")
        return _err(500, "internal_error", "Error interno del simulador")

    @app.get("/healthz", tags=["Simulación (control local)"])
    def healthz():
        return {"ok": True}

    app.include_router(routes_platform.router)
    app.include_router(routes_orch.router)
    app.include_router(routes_sim.router)
    if serve_static and WEB_DIR.exists():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


async def _clock_loop(sim):
    while True:
        await asyncio.sleep(0.05)
        if sim.due():
            try:
                await asyncio.to_thread(sim.tick_day)
            except Exception:
                log.exception("fallo en el reloj; se pausa")
                sim.pause()


def main():
    import uvicorn
    uvicorn.run(create_app(), host=HOST, port=PORT, log_level="warning")
