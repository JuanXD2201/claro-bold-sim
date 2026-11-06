"""Dependencias compartidas de las rutas."""
import os
import time
from dataclasses import dataclass

from fastapi import Depends, Request

from ..platform_mock import PlatformError


@dataclass
class Ctx:
    sim: object
    client: str
    gated: bool          # False para lecturas de la UI: no consumen límite de tasa ni sufren fallas inyectadas


def get_ctx(request: Request) -> Ctx:
    client = request.headers.get("x-client", "external")[:20]
    return Ctx(request.app.state.sim, client, not (client == "ui" and request.method == "GET"))


def simulate_latency(request: Request, ctx: Ctx = Depends(get_ctx)):
    if not ctx.gated:
        return
    ms = ctx.sim.platform.sample_latency()
    if os.environ.get("ORQ_FAKE_LATENCY"):
        request.state.fake_latency_ms = ms      # modo navegador: no se bloquea el hilo; el registro suma la latencia simulada
    else:
        time.sleep(ms / 1000)


def unwrap(result):
    """Convierte un resultado por elemento con 'error' en una excepción HTTP uniforme."""
    if isinstance(result, dict) and "error" in result:
        e = result["error"]
        raise PlatformError(e["status"], e["code"], e["message"])
    return result
