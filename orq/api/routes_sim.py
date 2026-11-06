"""Control de la simulación (reloj, escenarios, inyección de fallas, SSE) — prefijo /sim. Fuera del log de la API simulada."""
import asyncio
import json

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from ..config import SPEEDS
from ..scenario import apply_scenario, list_scenarios
from .deps import Ctx, get_ctx
from .schemas import FaultBody, LatencyBody, RateLimitBody, SpeedBody, SpikeBody

router = APIRouter(prefix="/sim", tags=["Simulación (control local)"])


@router.get("/state", summary="Reloj + configuración de la plataforma simulada")
def state(ctx: Ctx = Depends(get_ctx)):
    return {"clock": ctx.sim.clock(), "platform": ctx.sim.platform.config(), "speeds": list(SPEEDS)}


@router.post("/play")
def play(ctx: Ctx = Depends(get_ctx)):
    ctx.sim.play()
    return ctx.sim.clock()


@router.post("/pause")
def pause(ctx: Ctx = Depends(get_ctx)):
    ctx.sim.pause()
    return ctx.sim.clock()


@router.post("/speed")
def speed(body: SpeedBody, ctx: Ctx = Depends(get_ctx)):
    ctx.sim.set_speed(body.speed)
    return ctx.sim.clock()


@router.post("/tick", summary="Avanzar un día")
def tick(ctx: Ctx = Depends(get_ctx)):
    with ctx.sim.db.lock:
        return {"cycle": ctx.sim.tick_day(), "clock": ctx.sim.clock()}


@router.post("/advance-to-period-end", summary="Avanzar hasta el día 30")
def to_end(ctx: Ctx = Depends(get_ctx)):
    with ctx.sim.db.lock:
        ctx.sim.advance_to_period_end()
        return ctx.sim.clock()


@router.post("/close-period", summary="Reiniciar periodo (R4: desborde vuelve al origen)")
def close(ctx: Ctx = Depends(get_ctx)):
    with ctx.sim.db.lock:
        return {"result": ctx.sim.close_period(), "clock": ctx.sim.clock()}


@router.post("/reset", summary="Reiniciar todo con la misma semilla")
def reset(ctx: Ctx = Depends(get_ctx)):
    ctx.sim.reset()
    return ctx.sim.clock()


@router.get("/scenarios")
def scenarios():
    return {"items": list_scenarios()}


@router.post("/scenarios/{key}", summary="Activar un escenario")
def scenario(key: str, ctx: Ctx = Depends(get_ctx)):
    with ctx.sim.db.lock:
        return apply_scenario(ctx.sim, key)


@router.put("/latency")
def latency(body: LatencyBody, ctx: Ctx = Depends(get_ctx)):
    ctx.sim.platform.set_latency(body.latency_ms)
    ctx.sim.bump()
    return ctx.sim.platform.config()


@router.put("/rate-limit")
def rate_limit(body: RateLimitBody, ctx: Ctx = Depends(get_ctx)):
    ctx.sim.platform.set_rate_limit(body.enabled, body.max_calls, body.retry_after_s)
    ctx.sim.bump()
    return ctx.sim.platform.config()


@router.post("/faults", summary="Inyectar falla transitoria/permanente, global o por SIM")
def add_fault(body: FaultBody, ctx: Ctx = Depends(get_ctx)):
    f = ctx.sim.platform.add_fault(body.scope, body.iccid, body.kind, body.status, body.count, body.commit_then_fail)
    ctx.sim.bump()
    return f


@router.delete("/faults", summary="Limpiar todas las fallas")
def clear_faults(ctx: Ctx = Depends(get_ctx)):
    ctx.sim.platform.clear_faults()
    ctx.sim.bump()
    return ctx.sim.platform.config()


@router.delete("/faults/{fault_id}")
def clear_fault(fault_id: int, ctx: Ctx = Depends(get_ctx)):
    ctx.sim.platform.clear_faults(fault_id)
    ctx.sim.bump()
    return ctx.sim.platform.config()


@router.post("/spike", summary="Inyectar pico de consumo en una SIM")
def spike(body: SpikeBody, ctx: Ctx = Depends(get_ctx)):
    ctx.sim.platform.inject_spike(body.iccid, body.multiplier, body.days)
    ctx.sim.bump()
    return ctx.sim.platform.config()


@router.get("/api-log", summary="Registro de llamadas a la API simulada")
def api_log(since: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=500), ctx: Ctx = Depends(get_ctx)):
    return {"items": ctx.sim.apilog.list(since, limit)}


@router.delete("/api-log")
def clear_log(ctx: Ctx = Depends(get_ctx)):
    ctx.sim.apilog.clear()
    ctx.sim.bump()
    return {"ok": True}


@router.get("/events", summary="Eventos en vivo (SSE): emite cuando cambia el estado")
async def events(request: Request):
    sim = request.app.state.sim

    async def gen():
        last, idle = -1, 0
        while not await request.is_disconnected():
            v = sim.version
            if v != last:
                last, idle = v, 0
                yield f"event: state\ndata: {json.dumps({'version': v, 'clock': sim.clock()})}\n\n"
            else:
                idle += 1
                if idle % 60 == 0:
                    yield ": ping\n\n"
            await asyncio.sleep(0.25)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
