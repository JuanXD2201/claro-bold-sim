"""Orquestador — prefijo /orchestrator."""
import json
from typing import Optional

from fastapi import APIRouter, Depends, Query, Response

from ..engine import RateLimited
from ..platform_mock import PlatformError
from ..reports import monthly_report, report_csv
from .deps import Ctx, get_ctx, unwrap
from .schemas import IdsBody, ManualBulkBody, ManualMoveBody, RulesPatch

router = APIRouter(prefix="/orchestrator", tags=["Orquestador"])


# ------------------------------------------------------------------ estado
@router.get("/summary", summary="Tarjetas del resumen")
def summary(ctx: Ctx = Depends(get_ctx)):
    return ctx.sim.orch.summary()


@router.get("/pools", summary="Estado por pool: uso, proyección, SIMs, top consumidoras")
def pools(ctx: Ctx = Depends(get_ctx)):
    return {"items": ctx.sim.orch.pool_status()}


@router.get("/series", summary="Uso acumulado por pool y día + proyección")
def series(ctx: Ctx = Depends(get_ctx)):
    return ctx.sim.orch.series()


@router.get("/cycle", summary="Estado de ciclo (reloj, límite de tasa, métricas del motor)")
def cycle(ctx: Ctx = Depends(get_ctx)):
    s = ctx.sim
    ms = [m["cycle_ms"] for m in s.metrics]
    return {"clock": s.clock(), "platform": s.platform.config(),
            "metrics": {"cycles": len(ms), "last_ms": ms[-1] if ms else None,
                        "avg_ms": round(sum(ms) / len(ms), 2) if ms else None, "max_ms": max(ms) if ms else None,
                        "recent": list(s.metrics)[-30:]}}


@router.get("/sims/{iccid}/detail", summary="SIM + consumo + movimientos + alertas + auditoría")
def sim_detail(iccid: str, ctx: Ctx = Depends(get_ctx)):
    s = ctx.sim
    sim = s.platform.get_sim(iccid, gated=False)
    audit = s.db.all("SELECT * FROM audit WHERE entity_id=? OR detail LIKE ? ORDER BY id DESC LIMIT 100", (iccid, f'%{iccid}%'))
    return {"sim": sim, "usage": s.platform.usage(iccid=iccid, gated=False),
            "moves": s.orch.list_moves(iccid=iccid, limit=100),
            "alerts": s.db.all("SELECT * FROM alerts WHERE subject_id=? ORDER BY id DESC", (iccid,)),
            "audit": [_audit_row(a) for a in audit]}


# ------------------------------------------------------------------ reglas
@router.get("/rules", summary="Reglas vigentes")
def get_rules(ctx: Ctx = Depends(get_ctx)):
    return ctx.sim.orch.get_rules()


@router.put("/rules", summary="Actualizar reglas (campos parciales; se validan en conjunto)")
def put_rules(body: RulesPatch, ctx: Ctx = Depends(get_ctx)):
    with ctx.sim.db.lock:
        return ctx.sim.orch.set_rules(body.patch(), actor="user")


@router.post("/rules/preview", summary="Vista previa de movimientos con las reglas indicadas (no persiste)")
def preview(body: RulesPatch, ctx: Ctx = Depends(get_ctx)):
    return ctx.sim.orch.preview(body.patch())


# ------------------------------------------------------------------ movimientos
@router.get("/moves", summary="Cola de movimientos")
def list_moves(state: Optional[str] = Query(None, pattern="^(proposed|approved|executed|failed|rejected|reverted)$"),
               iccid: Optional[str] = None, batch_id: Optional[str] = None, limit: int = Query(500, ge=1, le=2000),
               ctx: Ctx = Depends(get_ctx)):
    return {"items": ctx.sim.orch.list_moves(state, iccid, batch_id, limit)}


@router.post("/moves", summary="Mover una SIM ahora (manual, auditado, con resultado de la API)")
def manual_move(body: ManualMoveBody, ctx: Ctx = Depends(get_ctx)):
    with ctx.sim.db.lock:
        return ctx.sim.orch.manual_move(body.iccid, body.pool_id, "user", body.reason)


@router.post("/moves/bulk", summary="Movimientos manuales por lote; resultado por elemento")
def manual_bulk(body: ManualBulkBody, ctx: Ctx = Depends(get_ctx)):
    with ctx.sim.db.lock:
        return ctx.sim.orch.bulk_manual([i.model_dump() for i in body.items], "user")


@router.post("/moves/approve", summary="Aprobar (y ejecutar) varios movimientos propuestos")
def approve_many(body: IdsBody, execute: bool = True, ctx: Ctx = Depends(get_ctx)):
    with ctx.sim.db.lock:
        return {"results": ctx.sim.orch.approve(body.ids, execute)}


@router.post("/moves/reject", summary="Rechazar varios movimientos propuestos")
def reject_many(body: IdsBody, ctx: Ctx = Depends(get_ctx)):
    with ctx.sim.db.lock:
        return {"results": ctx.sim.orch.reject(body.ids)}


@router.post("/moves/retry", summary="Reintentar movimientos fallidos")
def retry_many(body: IdsBody, ctx: Ctx = Depends(get_ctx)):
    with ctx.sim.db.lock:
        return {"results": ctx.sim.orch.retry(body.ids)}


@router.post("/moves/batches/{batch_id}/revert", summary="Revertir todos los movimientos ejecutados de un lote")
def revert_batch(batch_id: str, ctx: Ctx = Depends(get_ctx)):
    with ctx.sim.db.lock:
        return ctx.sim.orch.revert_batch(batch_id)


@router.get("/moves/{move_id}", summary="Detalle de un movimiento")
def get_move(move_id: int, ctx: Ctx = Depends(get_ctx)):
    return {**ctx.sim.orch.get_move(move_id), "api": ctx.sim.orch.last_api.get(move_id)}


@router.post("/moves/{move_id}/approve", summary="Aprobar un movimiento (y ejecutarlo)")
def approve(move_id: int, execute: bool = True, ctx: Ctx = Depends(get_ctx)):
    with ctx.sim.db.lock:
        return unwrap(ctx.sim.orch.approve([move_id], execute)[0])


@router.post("/moves/{move_id}/reject", summary="Rechazar un movimiento propuesto")
def reject(move_id: int, ctx: Ctx = Depends(get_ctx)):
    with ctx.sim.db.lock:
        return unwrap(ctx.sim.orch.reject([move_id])[0])


@router.post("/moves/{move_id}/execute", summary="Ejecutar un movimiento aprobado")
def execute(move_id: int, ctx: Ctx = Depends(get_ctx)):
    with ctx.sim.db.lock:
        try:
            return ctx.sim.orch.execute(move_id, "user")
        except RateLimited as e:
            return {"move": ctx.sim.orch.get_move(move_id), "deferred": True, "retry_after": e.retry_after}


@router.post("/moves/{move_id}/retry", summary="Reintentar un movimiento fallido")
def retry(move_id: int, ctx: Ctx = Depends(get_ctx)):
    with ctx.sim.db.lock:
        return unwrap(ctx.sim.orch.retry([move_id])[0])


@router.post("/moves/{move_id}/revert", summary="Revertir un movimiento ejecutado")
def revert(move_id: int, ctx: Ctx = Depends(get_ctx)):
    with ctx.sim.db.lock:
        return ctx.sim.orch.revert(move_id, "user")


# ------------------------------------------------------------------ alertas
@router.get("/alerts", summary="Alertas")
def list_alerts(state: Optional[str] = Query(None, pattern="^(open|acknowledged|resolved)$"),
                type: Optional[str] = Query(None, pattern="^(umbral|agotado|anomalia|api_failure|rate_limit|conciliacion)$"),
                severity: Optional[str] = Query(None, pattern="^(info|warning|critical)$"), ctx: Ctx = Depends(get_ctx)):
    return {"items": ctx.sim.orch.list_alerts(state, type, severity)}


@router.post("/alerts/acknowledge", summary="Reconocer alertas")
def ack(body: IdsBody, ctx: Ctx = Depends(get_ctx)):
    with ctx.sim.db.lock:
        return {"results": ctx.sim.orch.alert_action(body.ids, "acknowledged")}


@router.post("/alerts/resolve", summary="Resolver alertas")
def resolve(body: IdsBody, ctx: Ctx = Depends(get_ctx)):
    with ctx.sim.db.lock:
        return {"results": ctx.sim.orch.alert_action(body.ids, "resolved")}


# ------------------------------------------------------------------ auditoría
def _audit_row(a):
    a = dict(a)
    a["detail"] = json.loads(a["detail"]) if a.get("detail") else None
    return a


def _audit_query(ctx, actor, event, entity, date_from, date_to):
    w, p = [], []
    if actor:
        w.append("actor=?")
        p.append(actor)
    if event:
        w.append("event LIKE ?")
        p.append(event + "%")
    if entity:
        w.append("(entity_id=? OR entity=?)")
        p += [entity, entity]
    if date_from:
        w.append("ts >= ?")
        p.append(date_from)
    if date_to:
        w.append("ts < ?")
        p.append(date_to + "~")            # '~' > cualquier dígito/letra: incluye todo el día final
    return (" WHERE " + " AND ".join(w)) if w else "", p


@router.get("/audit", summary="Auditoría filtrable")
def audit(actor: Optional[str] = None, event: Optional[str] = None, entity: Optional[str] = None,
          date_from: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
          date_to: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
          limit: int = Query(200, ge=1, le=2000), offset: int = Query(0, ge=0), ctx: Ctx = Depends(get_ctx)):
    where, p = _audit_query(ctx, actor, event, entity, date_from, date_to)
    db = ctx.sim.db
    rows = db.all(f"SELECT * FROM audit{where} ORDER BY id DESC LIMIT ? OFFSET ?", p + [limit, offset])
    return {"items": [_audit_row(r) for r in rows], "total": db.scalar(f"SELECT COUNT(*) FROM audit{where}", p),
            "actors": [r["actor"] for r in db.all("SELECT DISTINCT actor FROM audit ORDER BY actor")]}


@router.get("/audit.csv", summary="Exportar auditoría a CSV", response_class=Response)
def audit_csv(actor: Optional[str] = None, event: Optional[str] = None, entity: Optional[str] = None,
              date_from: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
              date_to: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"), ctx: Ctx = Depends(get_ctx)):
    import csv
    import io
    where, p = _audit_query(ctx, actor, event, entity, date_from, date_to)
    rows = ctx.sim.db.all(f"SELECT * FROM audit{where} ORDER BY id", p)
    buf = io.StringIO()
    w = csv.writer(buf)
    cols = ["id", "ts", "period", "abs_day", "actor", "event", "entity", "entity_id", "detail"]
    w.writerow(cols)
    for r in rows:
        w.writerow([r[c] for c in cols])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=auditoria.csv"})


# ------------------------------------------------------------------ reporte mensual
@router.get("/reports/monthly", summary="Reporte mensual consolidado (JSON o CSV)")
def monthly(period: Optional[int] = Query(None, ge=1), format: str = Query("json", pattern="^(json|csv)$"),
            ctx: Ctx = Depends(get_ctx)):
    db = ctx.sim.db
    if period is not None and period > db.period:
        raise PlatformError(404, "period_not_found", f"El periodo {period} aún no existe (actual: {db.period})")
    rep = monthly_report(db, period)
    if format == "csv":
        return Response(report_csv(rep), media_type="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=reporte_periodo_{rep['period']}.csv"})
    return rep
