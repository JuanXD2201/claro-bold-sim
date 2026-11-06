"""Dependencias compartidas de las rutas."""
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


def simulate_latency(ctx: Ctx = Depends(get_ctx)):
    if ctx.gated:
        time.sleep(ctx.sim.platform.sample_latency() / 1000)


def unwrap(result):
    """Convierte un resultado por elemento con 'error' en una excepción HTTP uniforme."""
    if isinstance(result, dict) and "error" in result:
        e = result["error"]
        raise PlatformError(e["status"], e["code"], e["message"])
    return result
