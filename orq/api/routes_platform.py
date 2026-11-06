"""Plataforma de Claro (simulada) — prefijo /platform."""
from typing import Optional

from fastapi import APIRouter, Depends, Header, Query, Response

from .deps import Ctx, get_ctx, simulate_latency
from .schemas import BulkMoveBody, MoveBody

router = APIRouter(prefix="/platform", tags=["Plataforma Claro (simulada)"], dependencies=[Depends(simulate_latency)])
ICCID = r"^[0-9A-Za-z-]{1,32}$"


@router.get("/pools", summary="Pools con capacidad, consumo y excedente del periodo")
def pools(ctx: Ctx = Depends(get_ctx)):
    return {"items": ctx.sim.platform.list_pools(ctx.gated)}


@router.get("/sims", summary="Listar SIMs (filtros, orden y paginación)")
def sims(q: Optional[str] = Query(None, max_length=60, description="ICCID, MSISDN, comercio o datáfono"),
         pool_id: Optional[str] = None, status: Optional[str] = Query(None, pattern="^(activa|suspendida)$"),
         profile: Optional[str] = Query(None, pattern="^(bajo|medio|alto)$"), city: Optional[str] = None,
         anomaly: Optional[bool] = None, min_daily: Optional[float] = None, max_daily: Optional[float] = None,
         min_period: Optional[float] = None, max_period: Optional[float] = None,
         pending_move: Optional[bool] = None, recently_moved: Optional[bool] = None,
         sort: str = "iccid", order: str = Query("asc", pattern="^(asc|desc)$"),
         page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), ctx: Ctx = Depends(get_ctx)):
    return ctx.sim.platform.list_sims(q, pool_id, status, profile, city, anomaly, min_daily, max_daily, min_period,
                                      max_period, pending_move, recently_moved, sort, order, page, page_size, ctx.gated)


@router.get("/sims/{iccid}", summary="Detalle de una SIM")
def get_sim(iccid: str, ctx: Ctx = Depends(get_ctx)):
    return ctx.sim.platform.get_sim(iccid, ctx.gated)


@router.get("/usage", summary="Consumo diario por pool o por SIM")
def usage(period: Optional[int] = Query(None, ge=1), pool_id: Optional[str] = None,
          iccid: Optional[str] = Query(None, pattern=ICCID), ctx: Ctx = Depends(get_ctx)):
    return ctx.sim.platform.usage(period, pool_id, iccid, ctx.gated)


@router.post("/sims/bulk-move", summary="Mover varias SIMs; resultado por elemento (éxito parcial permitido)")
def bulk_move(body: BulkMoveBody, ctx: Ctx = Depends(get_ctx)):
    return ctx.sim.platform.bulk_move([m.model_dump() for m in body.moves], actor=ctx.client)


@router.post("/sims/{iccid}/move", summary="Mover una SIM (requiere Idempotency-Key)")
def move(body: MoveBody, response: Response, iccid: str,
         idempotency_key: Optional[str] = Header(None, max_length=80), ctx: Ctx = Depends(get_ctx)):
    res = ctx.sim.platform.move_sim(iccid, body.pool_id, idempotency_key, actor=ctx.client)
    if res["idempotent_replay"]:
        response.headers["Idempotent-Replay"] = "true"
    return res


@router.post("/sims/{iccid}/suspend", summary="Suspender SIM")
def suspend(iccid: str, ctx: Ctx = Depends(get_ctx)):
    return ctx.sim.platform.set_status(iccid, "suspendida", ctx.client)


@router.post("/sims/{iccid}/activate", summary="Activar SIM")
def activate(iccid: str, ctx: Ctx = Depends(get_ctx)):
    return ctx.sim.platform.set_status(iccid, "activa", ctx.client)
