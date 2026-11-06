"""Orquestador: reglas, propuestas, ejecución con reintentos/idempotencia, alertas y conciliación."""
from .config import (MAX_ATTEMPTS, OPERATIONAL_POOLS, OVERFLOW_POOL, PERIOD_DAYS, DEFAULT_RULES)
from .platform_mock import PlatformError
from .records import audit, raise_alert
from .rules import plan_moves, project, validate_rules

ACTIVE_MOVE = ("proposed", "approved")


class RateLimited(Exception):
    def __init__(self, retry_after):
        super().__init__("rate limited")
        self.retry_after = retry_after


class Orchestrator:
    def __init__(self, db, platform, apilog):
        self.db, self.platform, self.apilog = db, platform, apilog
        self.last_api = {}            # move_id -> último resultado de API (para la UI)
        self._batch_seq = 0

    # ------------------------------------------------------------ reglas
    def get_rules(self):
        return {**DEFAULT_RULES, **self.db.kv_get("rules", {})}

    def set_rules(self, patch, actor="user"):
        new = validate_rules({**self.get_rules(), **patch})
        old = self.get_rules()
        self.db.kv_set("rules", new)
        audit(self.db, actor, "rules.updated", "rules", "rules",
              {k: [old[k], new[k]] for k in new if old[k] != new[k]})
        return new

    # ------------------------------------------------------------ estado / proyección
    def _state(self):
        """Estado por pool aplicando virtualmente los movimientos pendientes."""
        day = self.db.day
        used = {r["pool_id"]: r["mb"] for r in self.db.all(
            "SELECT pool_id, SUM(mb) AS mb FROM usage_daily WHERE period=? GROUP BY pool_id", (self.db.period,))}
        pending = {m["iccid"]: m for m in self.db.all("SELECT iccid,to_pool FROM moves WHERE state IN ('proposed','approved')")}
        cd = self.get_rules()["cooldown_days"]
        state = {p["id"]: {"cap": p["capacity_mb"], "used": used.get(p["id"], 0.0), "members": []}
                 for p in self.db.all("SELECT * FROM pools")}
        for s in self.db.all("SELECT * FROM sims"):
            avg = s["period_mb"] / day if day > 0 else s["base_rate"]
            pm = pending.get(s["iccid"])
            recent = s["last_move_day"] is not None and self.db.abs_day - s["last_move_day"] <= cd
            pid = pm["to_pool"] if pm else s["pool_id"]
            state[pid]["members"].append({"iccid": s["iccid"], "avg": avg, "active": s["status"] == "activa",
                                          "blocked": bool(pm) or recent or s["anomaly"] == 1})
        return state

    def pool_status(self):
        day, days_left = self.db.day, PERIOD_DAYS - self.db.day
        rules = self.get_rules()
        state = self._state()
        real = {p["id"]: p for p in self.platform.pools_with_usage()}
        top = {}
        for s in self.db.all("SELECT iccid,merchant,pool_id,period_mb,daily_mb,profile FROM sims ORDER BY period_mb DESC"):
            top.setdefault(s["pool_id"], [])
            if len(top[s["pool_id"]]) < 5:
                top[s["pool_id"]].append(s)
        pend_in, pend_out = {}, {}
        for m in self.db.all("SELECT from_pool,to_pool FROM moves WHERE state IN ('proposed','approved')"):
            pend_in[m["to_pool"]] = pend_in.get(m["to_pool"], 0) + 1
            pend_out[m["from_pool"]] = pend_out.get(m["from_pool"], 0) + 1
        out = []
        for pid, p in real.items():
            cap = p["capacity_mb"]
            proj = None
            if day > 0:
                proj = round(project(self._real_pool_for_projection(pid), days_left), 1)
            ratio = p["used_mb"] / cap if cap else None
            pratio = proj / cap if (cap and proj is not None) else None
            level = "ok"
            if cap:
                m = pratio if (rules["trigger"] == "projected" and pratio is not None and day >= rules["min_history_days"]) else ratio
                level = "critical" if ratio >= rules["critical_threshold"] else ("warn" if (m or 0) >= rules["warn_threshold"] else "ok")
            out.append({**p, "ratio": ratio, "projected_mb": proj, "projected_ratio": pratio, "level": level,
                        "top_sims": top.get(pid, []), "pending_in": pend_in.get(pid, 0), "pending_out": pend_out.get(pid, 0),
                        "daily_rate_mb": round(sum(m["avg"] for m in state[pid]["members"] if m["active"]), 2) if day else None})
        return out

    def _real_pool_for_projection(self, pid):
        """Proyección con los miembros reales actuales (sin movimientos pendientes)."""
        used = self.db.scalar("SELECT COALESCE(SUM(mb),0) FROM usage_daily WHERE period=? AND pool_id=?", (self.db.period, pid))
        day = max(self.db.day, 1)
        mem = [{"avg": s["period_mb"] / day, "active": s["status"] == "activa"} for s in
               self.db.all("SELECT period_mb,status FROM sims WHERE pool_id=?", (pid,))]
        return {"used": used, "members": mem}

    def preview(self, rules_override=None):
        rules = validate_rules({**self.get_rules(), **(rules_override or {})})
        props = plan_moves(self._state(), rules, self.db.day)
        sims = {s["iccid"]: s for s in self.db.all("SELECT iccid,merchant FROM sims")}
        return {"rules": rules, "day": self.db.day, "proposals": [{**p, "merchant": sims[p["iccid"]]["merchant"]} for p in props],
                "note": ("Sin historial suficiente para proyectar" if rules["trigger"] == "projected"
                         and self.db.day < rules["min_history_days"] else None)}

    # ------------------------------------------------------------ movimientos
    def _new_batch(self, prefix="B"):
        self._batch_seq += 1
        return f"{prefix}{self.db.period}-{self.db.day}-{self._batch_seq}"

    def _insert_move(self, iccid, frm, to, state, kind, reason, batch, actor, reverts_id=None):
        cur = self.db.exec(
            "INSERT INTO moves(iccid,from_pool,to_pool,state,kind,reason,batch_id,reverts_id,actor,period,created_day,updated_day)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (iccid, frm, to, state, kind, reason, batch, reverts_id, actor, self.db.period, self.db.abs_day, self.db.abs_day))
        mid = cur.lastrowid
        self.db.exec("UPDATE moves SET idem_key=? WHERE id=?", (f"mv-{mid}", mid))
        audit(self.db, actor, f"move.{state}", "move", mid, {"iccid": iccid, "from": frm, "to": to, "kind": kind})
        return mid

    def get_move(self, mid):
        m = self.db.one("SELECT * FROM moves WHERE id=?", (mid,))
        if not m:
            raise PlatformError(404, "move_not_found", f"Movimiento {mid} no existe")
        return m

    def _set_state(self, mid, state, error=None, actor="orchestrator", event=None):
        self.db.exec("UPDATE moves SET state=?, last_error=?, updated_day=? WHERE id=?", (state, error, self.db.abs_day, mid))
        audit(self.db, actor, event or f"move.{state}", "move", mid, {"error": error} if error else None)

    def list_moves(self, state=None, iccid=None, batch_id=None, limit=500):
        w, p = [], []
        for col, v in (("state", state), ("iccid", iccid), ("batch_id", batch_id)):
            if v:
                w.append(f"m.{col}=?")
                p.append(v)
        where = (" WHERE " + " AND ".join(w)) if w else ""
        return self.db.all("SELECT m.*, s.merchant, s.terminal_id FROM moves m LEFT JOIN sims s ON s.iccid=m.iccid"
                           f"{where} ORDER BY m.id DESC LIMIT ?", p + [limit])

    def propose_cycle(self, actor="orchestrator"):
        rules = self.get_rules()
        props = plan_moves(self._state(), rules, self.db.day)
        if not props:
            return []
        batch = self._new_batch("R")
        ids = [self._insert_move(p["iccid"], p["from_pool"], p["to_pool"], "proposed", "rule", p["reason"], batch, actor)
               for p in props]
        return ids

    def approve(self, ids, execute=True, actor="user"):
        results = []
        for mid in ids:
            try:
                m = self.get_move(mid)
                if m["state"] != "proposed":
                    raise PlatformError(409, "invalid_state", f"Movimiento {mid} está '{m['state']}', no 'proposed'")
                self._set_state(mid, "approved", actor=actor)
                results.append(self.execute(mid, actor) if execute else {"move": self.get_move(mid)})
            except PlatformError as e:
                results.append({"id": mid, "error": e.body()["error"]})
            except RateLimited as e:
                results.append({"move": self.get_move(mid), "deferred": True, "retry_after": e.retry_after})
        return results

    def reject(self, ids, actor="user"):
        out = []
        for mid in ids:
            m = self.get_move(mid)
            if m["state"] != "proposed":
                out.append({"id": mid, "error": {"code": "invalid_state", "status": 409,
                                                 "message": f"Movimiento {mid} está '{m['state']}'"}})
                continue
            self._set_state(mid, "rejected", actor=actor)
            out.append({"move": self.get_move(mid)})
        return out

    def retry(self, ids, actor="user"):
        out = []
        for mid in ids:
            m = self.get_move(mid)
            if m["state"] != "failed":
                out.append({"id": mid, "error": {"code": "invalid_state", "status": 409,
                                                 "message": f"Movimiento {mid} está '{m['state']}', no 'failed'"}})
                continue
            self.db.exec("UPDATE moves SET attempts=0 WHERE id=?", (mid,))
            self._set_state(mid, "approved", actor=actor, event="move.retry_requested")
            try:
                out.append(self.execute(mid, actor))
            except RateLimited as e:
                out.append({"move": self.get_move(mid), "deferred": True, "retry_after": e.retry_after})
        return out

    def manual_move(self, iccid, pool_id, actor="user", reason=None, batch=None):
        sim = self.db.one("SELECT * FROM sims WHERE iccid=?", (iccid,))
        if not sim:
            raise PlatformError(404, "sim_not_found", f"SIM {iccid} no existe")
        if not self.db.one("SELECT 1 FROM pools WHERE id=?", (pool_id,)):
            raise PlatformError(422, "unknown_pool", f"Pool {pool_id} no existe")
        if sim["pool_id"] == pool_id:
            raise PlatformError(409, "already_in_pool", f"SIM {iccid} ya está en {pool_id}")
        if sim["status"] != "activa":
            raise PlatformError(409, "sim_suspended", f"SIM {iccid} está suspendida")
        if self.db.scalar("SELECT 1 FROM moves WHERE iccid=? AND state IN ('proposed','approved') LIMIT 1", (iccid,)):
            raise PlatformError(409, "move_pending", f"SIM {iccid} ya tiene un movimiento pendiente")
        mid = self._insert_move(iccid, sim["pool_id"], pool_id, "approved", "manual", reason or "Movimiento manual",
                                batch or self._new_batch("M"), actor)
        try:
            return self.execute(mid, actor)
        except RateLimited as e:
            return {"move": self.get_move(mid), "deferred": True, "retry_after": e.retry_after}

    def bulk_manual(self, items, actor="user"):
        batch = self._new_batch("M")
        res = []
        for it in items:
            try:
                r = self.manual_move(it["iccid"], it["pool_id"], actor, it.get("reason"), batch)
                res.append({"iccid": it["iccid"], "ok": r["move"]["state"] in ("executed", "approved"), **r})
            except PlatformError as e:
                res.append({"iccid": it["iccid"], "ok": False, **e.body()})
        return {"batch_id": batch, "results": res,
                "summary": {"total": len(res), "ok": sum(1 for r in res if r["ok"]), "failed": sum(1 for r in res if not r["ok"])}}

    def revert(self, mid, actor="user"):
        m = self.get_move(mid)
        if m["state"] != "executed" or m["kind"] == "revert":
            raise PlatformError(409, "invalid_state", f"Movimiento {mid} no es revertible (estado '{m['state']}', tipo '{m['kind']}')")
        sim = self.db.one("SELECT pool_id FROM sims WHERE iccid=?", (m["iccid"],))
        if sim["pool_id"] != m["to_pool"]:
            raise PlatformError(409, "stale_move", f"SIM {m['iccid']} ya no está en {m['to_pool']}")
        rid = self._insert_move(m["iccid"], m["to_pool"], m["from_pool"], "approved", "revert",
                                f"Reversión del movimiento {mid}", self._new_batch("V"), actor, reverts_id=mid)
        try:
            r = self.execute(rid, actor)
        except RateLimited as e:
            return {"move": self.get_move(rid), "deferred": True, "retry_after": e.retry_after}
        if r["move"]["state"] == "executed":
            self._set_state(mid, "reverted", actor=actor)
        return r

    def revert_batch(self, batch_id, actor="user"):
        moves = [m for m in self.list_moves(batch_id=batch_id) if m["state"] == "executed" and m["kind"] != "revert"]
        out = []
        for m in moves:
            try:
                out.append(self.revert(m["id"], actor))
            except PlatformError as e:
                out.append({"id": m["id"], "error": e.body()["error"]})
        return {"batch_id": batch_id, "results": out}

    # ------------------------------------------------------------ ejecución contra la plataforma
    def _call(self, m, key):
        lat = self.platform.sample_latency()
        req = {"pool_id": m["to_pool"], "Idempotency-Key": key}
        try:
            res = self.platform.move_sim(m["iccid"], m["to_pool"], key, actor="orchestrator")
            self.apilog.add(source="orchestrator", method="POST", path=f"/platform/sims/{m['iccid']}/move", status=200,
                            latency_ms=lat, request=req, response=res)
            return res, 200, lat
        except PlatformError as e:
            self.apilog.add(source="orchestrator", method="POST", path=f"/platform/sims/{m['iccid']}/move",
                            status=e.status, latency_ms=lat, request=req, response=e.body(), retry_after=e.retry_after)
            raise

    def execute(self, mid, actor="orchestrator"):
        m = self.get_move(mid)
        if m["state"] != "approved":
            raise PlatformError(409, "invalid_state", f"Movimiento {mid} está '{m['state']}', no 'approved'")
        sim = self.db.one("SELECT pool_id FROM sims WHERE iccid=?", (m["iccid"],))
        if sim["pool_id"] == m["to_pool"]:
            self._set_state(mid, "executed", actor=actor, event="move.adopted")
            return {"move": self.get_move(mid), "api": None}
        if sim["pool_id"] != m["from_pool"]:
            self._set_state(mid, "failed", f"Desactualizado: la SIM ahora está en {sim['pool_id']}", actor)
            return {"move": self.get_move(mid), "api": None}
        key = m["idem_key"] or f"mv-{mid}"
        attempts = 0
        while True:
            attempts += 1
            self.db.exec("UPDATE moves SET attempts=attempts+1 WHERE id=?", (mid,))
            try:
                res, status, lat = self._call(m, key)
            except PlatformError as e:
                self.last_api[mid] = {"status": e.status, "body": e.body()}
                if e.status == 429:
                    raise_alert(self.db, "rate_limit", "warning", "api", "platform",
                                f"Límite de tasa alcanzado (Retry-After {e.retry_after}s); movimientos diferidos")
                    audit(self.db, "orchestrator", "move.deferred", "move", mid, {"retry_after": e.retry_after})
                    raise RateLimited(e.retry_after)
                if e.status in (500, 503) and attempts < MAX_ATTEMPTS:
                    audit(self.db, "orchestrator", "move.retry", "move", mid,
                          {"attempt": attempts, "status": e.status, "backoff_s": 2 ** attempts})
                    continue
                self._set_state(mid, "failed", f"{e.status} {e.code}: {e.message}", actor)
                raise_alert(self.db, "api_failure", "critical", "sim", m["iccid"],
                            f"Movimiento {mid} falló tras {attempts} intento(s): {e.status} {e.code}")
                return {"move": self.get_move(mid), "api": self.last_api[mid]}
            self.last_api[mid] = {"status": status, "latency_ms": lat, "body": res}
            self._set_state(mid, "executed", actor=actor)
            if attempts > 1:
                raise_alert(self.db, "api_failure", "info", "sim", m["iccid"],
                            f"Movimiento {mid} recuperado tras {attempts} intentos (falla transitoria)")
            return {"move": self.get_move(mid), "api": self.last_api[mid]}

    def execute_pending(self, ids=None, actor="orchestrator"):
        """Ejecuta movimientos aprobados (p. ej. diferidos por 429). Se detiene al recibir 429."""
        rows = self.db.all("SELECT id FROM moves WHERE state='approved' ORDER BY id")
        done = 0
        for r in rows:
            if ids is not None and r["id"] not in ids:
                continue
            try:
                self.execute(r["id"], actor)
                done += 1
            except RateLimited:
                break
        return done

    # ------------------------------------------------------------ alertas / conciliación
    def evaluate_alerts(self):
        rules, day = self.get_rules(), self.db.day
        for p in self.pool_status():
            if not p["capacity_mb"]:
                continue
            if p["ratio"] >= rules["critical_threshold"]:
                raise_alert(self.db, "agotado", "critical", "pool", p["id"],
                            f"{p['id']} agotado: {p['used_mb']:.0f}/{p['capacity_mb']:.0f} MB ({p['ratio']:.0%})")
            use_proj = rules["trigger"] == "projected" and day >= rules["min_history_days"] and p["projected_ratio"] is not None
            m = p["projected_ratio"] if use_proj else p["ratio"]
            if m >= rules["warn_threshold"]:
                raise_alert(self.db, "umbral", "warning", "pool", p["id"],
                            f"{p['id']} supera el umbral {rules['warn_threshold']:.0%} "
                            f"({'proyectado' if use_proj else 'real'} {m:.0%})")
        for s in self.db.all("SELECT iccid,daily_mb,base_rate FROM sims WHERE anomaly=1"):
            raise_alert(self.db, "anomalia", "warning", "sim", s["iccid"],
                        f"Consumo anómalo: {s['daily_mb']:.2f} MB/día (perfil {s['base_rate']} MB/día)")

    def reconcile(self, actor="orchestrator"):
        """Compara lo que el orquestador cree con el estado de la plataforma. Plataforma = verdad."""
        fixed = []
        with self.db.tx():
            for s in self.db.all("SELECT iccid,pool_id,origin_pool FROM sims"):
                last = self.db.one("SELECT * FROM moves WHERE iccid=? AND state='executed' ORDER BY id DESC LIMIT 1", (s["iccid"],))
                expected = last["to_pool"] if last else s["origin_pool"]
                if expected == s["pool_id"]:
                    continue
                pend = self.db.one("SELECT * FROM moves WHERE iccid=? AND state IN ('failed','approved') AND to_pool=? ORDER BY id DESC LIMIT 1",
                                   (s["iccid"], s["pool_id"]))
                if pend:      # se aplicó en la plataforma aunque el orquestador no vio la respuesta
                    self._set_state(pend["id"], "executed", actor=actor, event="reconcile.healed")
                    fixed.append({"iccid": s["iccid"], "action": "healed", "move": pend["id"]})
                else:         # cambio externo: se registra y se adopta
                    mid = self._insert_move(s["iccid"], expected, s["pool_id"], "executed", "external",
                                            "Cambio detectado fuera del orquestador", self._new_batch("X"), "platform")
                    raise_alert(self.db, "conciliacion", "warning", "sim", s["iccid"],
                                f"Deriva: la plataforma tiene la SIM en {s['pool_id']}, el orquestador esperaba {expected}")
                    audit(self.db, actor, "reconcile.drift", "sim", s["iccid"], {"expected": expected, "actual": s["pool_id"]})
                    fixed.append({"iccid": s["iccid"], "action": "adopted", "move": mid})
        return fixed

    # ------------------------------------------------------------ ciclo
    def cycle(self):
        """Un ciclo de orquestación tras generar el consumo del día."""
        rules = self.get_rules()
        stats = {"reconciled": len(self.reconcile())}
        self.evaluate_alerts()
        stats["executed_pending"] = self.execute_pending()
        ids = self.propose_cycle()
        stats["proposed"] = len(ids)
        if ids and rules["mode"] == "automatic":
            for mid in ids:
                self._set_state(mid, "approved", actor="orchestrator", event="move.auto_approved")
            stats["auto_executed"] = self.execute_pending(set(ids))
        return stats

    def close_period_returns(self):
        """R4: SIMs en desborde vuelven a su pool de origen al cerrar el periodo."""
        sims = self.db.all("SELECT iccid,origin_pool FROM sims WHERE pool_id=? AND origin_pool<>?", (OVERFLOW_POOL, OVERFLOW_POOL))
        if not sims:
            return 0
        batch = self._new_batch("R4-")
        for s in sims:
            self._insert_move(s["iccid"], OVERFLOW_POOL, s["origin_pool"], "approved", "return",
                              "R4: retorno desde desborde al cierre del periodo", batch, "orchestrator")
        return self.execute_pending(actor="orchestrator")

    # ------------------------------------------------------------ alertas (acciones)
    def list_alerts(self, state=None, type_=None, severity=None, limit=500):
        w, p = [], []
        for col, v in (("state", state), ("type", type_), ("severity", severity)):
            if v:
                w.append(f"{col}=?")
                p.append(v)
        where = (" WHERE " + " AND ".join(w)) if w else ""
        return self.db.all(f"SELECT * FROM alerts{where} ORDER BY id DESC LIMIT ?", p + [limit])

    def alert_action(self, ids, new_state, actor="user"):
        out = []
        for aid in ids:
            a = self.db.one("SELECT * FROM alerts WHERE id=?", (aid,))
            if not a:
                out.append({"id": aid, "error": {"code": "alert_not_found", "status": 404, "message": f"Alerta {aid} no existe"}})
                continue
            if a["state"] == "resolved" or (new_state == "acknowledged" and a["state"] != "open"):
                out.append({"id": aid, "error": {"code": "invalid_state", "status": 409,
                                                 "message": f"Alerta {aid} está '{a['state']}'"}})
                continue
            self.db.exec("UPDATE alerts SET state=?, updated_day=? WHERE id=?", (new_state, self.db.abs_day, aid))
            audit(self.db, actor, f"alert.{new_state}", "alert", aid)
            out.append({"alert": self.db.one("SELECT * FROM alerts WHERE id=?", (aid,))})
        return out

    def auto_resolve_period_alerts(self):
        self.db.exec("UPDATE alerts SET state='resolved', updated_day=? WHERE state IN ('open','acknowledged') "
                     "AND type IN ('umbral','agotado','anomalia')", (self.db.abs_day,))

    # ------------------------------------------------------------ resumen
    def summary(self):
        pools = self.platform.pools_with_usage()
        active = self.db.scalar("SELECT COUNT(*) FROM sims WHERE status='activa'")
        return {"active_sims": active, "total_sims": self.db.scalar("SELECT COUNT(*) FROM sims"),
                "total_used_mb": round(sum(p["used_mb"] for p in pools), 3),
                "total_excess_mb": round(sum(p["excess_mb"] for p in pools), 3),
                "pending_moves": self.db.scalar("SELECT COUNT(*) FROM moves WHERE state='proposed'"),
                "approved_moves": self.db.scalar("SELECT COUNT(*) FROM moves WHERE state='approved'"),
                "failed_moves": self.db.scalar("SELECT COUNT(*) FROM moves WHERE state='failed'"),
                "open_alerts": self.db.scalar("SELECT COUNT(*) FROM alerts WHERE state='open'"),
                "mode": self.get_rules()["mode"]}

    def series(self):
        """Uso acumulado por pool y día del periodo actual + proyección lineal hasta el día 30."""
        rows = self.db.all("SELECT day, pool_id, SUM(mb) AS mb FROM usage_daily WHERE period=? GROUP BY day, pool_id ORDER BY day",
                           (self.db.period,))
        pids = OPERATIONAL_POOLS + (OVERFLOW_POOL,)
        out = {p: [{"day": 0, "mb": 0.0}] for p in pids}
        run = {p: 0.0 for p in pids}
        by_day = {}
        for r in rows:
            by_day.setdefault(r["day"], {})[r["pool_id"]] = r["mb"]
        for d in range(1, self.db.day + 1):
            for p in pids:
                run[p] += by_day.get(d, {}).get(p, 0.0)
                out[p].append({"day": d, "mb": round(run[p], 2)})
        proj = {p["id"]: p["projected_mb"] for p in self.pool_status()}
        return {"day": self.db.day, "period_days": PERIOD_DAYS, "series": out, "projected": proj,
                "capacity": {p["id"]: p["capacity_mb"] for p in self.platform.pools_with_usage()}}
