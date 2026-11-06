"""Plataforma de Claro simulada: pools, SIMs, tráfico, idempotencia, límite de tasa y fallas inyectables."""
import json
import random

from .config import ANOMALY_FACTOR, DEFAULT_LATENCY_MS, DEFAULT_RATE_LIMIT, NOISE, SEED
from .records import audit

RECENT_DAYS = 3
SORTABLE = {"iccid", "msisdn", "merchant", "terminal_id", "city", "pool_id", "status", "profile",
            "daily_mb", "period_mb", "last_move_day", "plan"}


class PlatformError(Exception):
    def __init__(self, status, code, message, retry_after=None, details=None):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.retry_after, self.details = retry_after, details

    def body(self):
        return {"error": {"code": self.code, "message": self.message, "status": self.status,
                          "details": self.details}}


class PlatformMock:
    def __init__(self, db, seed=SEED):
        self.db, self.seed = db, seed
        self.reset_config()

    # ---------------- configuración inyectable ----------------
    def reset_config(self):
        self.faults, self._fault_seq, self._bulk_seq = [], 0, 0
        self.latency_ms = DEFAULT_LATENCY_MS
        self.rate_limit = dict(DEFAULT_RATE_LIMIT)
        self.calls_in_cycle = 0
        self.spikes = {}
        self._rng = random.Random(self.seed)

    def config(self):
        return {"latency_ms": self.latency_ms, "rate_limit": dict(self.rate_limit),
                "calls_in_cycle": self.calls_in_cycle, "faults": [dict(f) for f in self.faults],
                "spikes": {k: dict(v) for k, v in self.spikes.items()}}

    def set_latency(self, ms):
        self.latency_ms = ms

    def set_rate_limit(self, enabled, max_calls, retry_after_s):
        self.rate_limit = {"enabled": enabled, "max_calls": max_calls, "retry_after_s": retry_after_s}

    def add_fault(self, scope="global", iccid=None, kind="transient", status=None, count=3, commit_then_fail=False):
        if scope == "sim" and not self._sim(iccid):
            raise PlatformError(404, "sim_not_found", f"SIM {iccid} no existe")
        status = status or (503 if kind == "transient" else 500)
        self._fault_seq += 1
        f = {"id": self._fault_seq, "scope": scope, "iccid": iccid if scope == "sim" else None, "kind": kind,
             "status": status, "remaining": count if kind == "transient" else None,
             "commit_then_fail": bool(commit_then_fail), "hits": 0}
        self.faults.append(f)
        return f

    def clear_faults(self, fault_id=None):
        self.faults = [f for f in self.faults if fault_id is not None and f["id"] != fault_id]

    def new_cycle(self):
        self.calls_in_cycle = 0

    def sample_latency(self):
        return round(self.latency_ms * (0.7 + 0.6 * self._rng.random()), 1)

    def inject_spike(self, iccid, multiplier, days):
        if not self._sim(iccid):
            raise PlatformError(404, "sim_not_found", f"SIM {iccid} no existe")
        self.spikes[iccid] = {"multiplier": multiplier, "days_left": days}

    # ---------------- núcleo de fallas / límite ----------------
    def _gate(self, iccid=None):
        self.calls_in_cycle += 1
        rl = self.rate_limit
        if rl["enabled"] and self.calls_in_cycle > rl["max_calls"]:
            raise PlatformError(429, "rate_limited", f"Límite de {rl['max_calls']} llamadas por ciclo excedido",
                                retry_after=rl["retry_after_s"])
        f = next((x for x in self.faults if x["scope"] == "sim" and x["iccid"] == iccid), None) \
            or next((x for x in self.faults if x["scope"] == "global"), None)
        if f is None:
            return None
        f["hits"] += 1
        snapshot = dict(f)
        if f["remaining"] is not None:
            f["remaining"] -= 1
            if f["remaining"] <= 0:
                self.faults.remove(f)
        return snapshot

    @staticmethod
    def _fault_error(f):
        code = "transient_failure" if f["kind"] == "transient" else "permanent_failure"
        kind = "transitoria" if f["kind"] == "transient" else "permanente"
        return PlatformError(f["status"], code, f"Falla {kind} inyectada en la plataforma", details={"fault_id": f["id"]})

    def _sim(self, iccid):
        return self.db.one("SELECT * FROM sims WHERE iccid=?", (iccid,))

    def _need_sim(self, iccid):
        s = self._sim(iccid)
        if not s:
            raise PlatformError(404, "sim_not_found", f"SIM {iccid} no existe")
        return s

    # ---------------- lecturas ----------------
    def list_pools(self, gated=True):
        with self.db.lock:
            if gated:
                f = self._gate()
                if f:
                    raise self._fault_error(f)
            return self.pools_with_usage()

    def pools_with_usage(self):
        used = {r["pool_id"]: r["mb"] for r in self.db.all(
            "SELECT pool_id, SUM(mb) AS mb FROM usage_daily WHERE period=? GROUP BY pool_id", (self.db.period,))}
        counts = {r["pool_id"]: r["n"] for r in self.db.all("SELECT pool_id, COUNT(*) AS n FROM sims GROUP BY pool_id")}
        out = []
        for p in self.db.all("SELECT * FROM pools ORDER BY is_overflow, id"):
            u = round(used.get(p["id"], 0.0), 3)
            out.append({"id": p["id"], "capacity_mb": p["capacity_mb"], "is_overflow": bool(p["is_overflow"]),
                        "sim_count": counts.get(p["id"], 0), "used_mb": u,
                        "excess_mb": round(max(0.0, u - p["capacity_mb"]), 3)})
        return out

    def get_sim(self, iccid, gated=True):
        with self.db.lock:
            if gated:
                f = self._gate(iccid)
                if f:
                    raise self._fault_error(f)
            return self._decorate(self._need_sim(iccid))

    def _decorate(self, s):
        s = dict(s)
        s["anomaly"] = bool(s["anomaly"])
        s["recently_moved"] = s["last_move_day"] is not None and self.db.abs_day - s["last_move_day"] <= RECENT_DAYS
        s["pending_move"] = bool(self.db.scalar(
            "SELECT 1 FROM moves WHERE iccid=? AND state IN ('proposed','approved') LIMIT 1", (s["iccid"],)))
        return s

    def list_sims(self, q=None, pool_id=None, status=None, profile=None, city=None, anomaly=None,
                  min_daily=None, max_daily=None, min_period=None, max_period=None, pending_move=None,
                  recently_moved=None, sort="iccid", order="asc", page=1, page_size=50, gated=True):
        with self.db.lock:
            if gated:
                f = self._gate()
                if f:
                    raise self._fault_error(f)
            w, p = [], []
            if q:
                like = f"%{q}%"
                w.append("(iccid LIKE ? OR msisdn LIKE ? OR merchant LIKE ? OR terminal_id LIKE ?)")
                p += [like] * 4
            for col, val in (("pool_id", pool_id), ("status", status), ("profile", profile), ("city", city)):
                if val:
                    w.append(f"{col}=?")
                    p.append(val)
            if anomaly is not None:
                w.append("anomaly=?")
                p.append(int(anomaly))
            for col, op, val in (("daily_mb", ">=", min_daily), ("daily_mb", "<=", max_daily),
                                 ("period_mb", ">=", min_period), ("period_mb", "<=", max_period)):
                if val is not None:
                    w.append(f"{col}{op}?")
                    p.append(val)
            pend = "EXISTS(SELECT 1 FROM moves m WHERE m.iccid=sims.iccid AND m.state IN ('proposed','approved'))"
            if pending_move is not None:
                w.append(pend if pending_move else f"NOT {pend}")
            rec = "(last_move_day IS NOT NULL AND ? - last_move_day <= ?)"
            if recently_moved is not None:
                w.append(rec if recently_moved else f"NOT {rec}")
                p += [self.db.abs_day, RECENT_DAYS]
            where = (" WHERE " + " AND ".join(w)) if w else ""
            sort = sort if sort in SORTABLE else "iccid"
            direction = "DESC" if str(order).lower() == "desc" else "ASC"
            total = self.db.scalar(f"SELECT COUNT(*) FROM sims{where}", p)
            rows = self.db.all(f"SELECT * FROM sims{where} ORDER BY {sort} {direction}, iccid LIMIT ? OFFSET ?",
                               p + [page_size, (page - 1) * page_size])
            return {"items": [self._decorate(r) for r in rows], "total": total, "page": page, "page_size": page_size}

    def usage(self, period=None, pool_id=None, iccid=None, gated=True):
        with self.db.lock:
            if gated:
                f = self._gate(iccid)
                if f:
                    raise self._fault_error(f)
            period = period or self.db.period
            if iccid:
                self._need_sim(iccid)
                rows = self.db.all("SELECT day, pool_id, mb FROM usage_daily WHERE period=? AND iccid=? ORDER BY day",
                                   (period, iccid))
            else:
                w, p = "period=?", [period]
                if pool_id:
                    w += " AND pool_id=?"
                    p.append(pool_id)
                rows = self.db.all(f"SELECT day, pool_id, ROUND(SUM(mb),3) AS mb FROM usage_daily WHERE {w} "
                                   "GROUP BY day, pool_id ORDER BY day, pool_id", p)
            return {"period": period, "rows": rows, "total_mb": round(sum(r["mb"] for r in rows), 3)}

    # ---------------- escrituras ----------------
    def move_sim(self, iccid, pool_id, idem_key, actor="api"):
        if not idem_key:
            raise PlatformError(400, "missing_idempotency_key", "Falta la cabecera Idempotency-Key")
        with self.db.lock:
            fault = self._gate(iccid)
            if fault and not fault["commit_then_fail"]:
                raise self._fault_error(fault)
            sim = self._need_sim(iccid)
            if not self.db.one("SELECT 1 FROM pools WHERE id=?", (pool_id,)):
                raise PlatformError(422, "unknown_pool", f"Pool {pool_id} no existe")
            prev = self.db.one("SELECT * FROM idem WHERE key=?", (idem_key,))
            if prev:
                if prev["iccid"] != iccid or prev["pool_id"] != pool_id:
                    raise PlatformError(409, "idempotency_key_reused", "Idempotency-Key ya usada con otro cuerpo")
                res = json.loads(prev["response"])
                res["idempotent_replay"] = True
                if fault:
                    raise self._fault_error(fault)
                return res
            if sim["status"] != "activa":
                raise PlatformError(409, "sim_suspended", f"SIM {iccid} está suspendida")
            if sim["pool_id"] == pool_id:
                raise PlatformError(409, "already_in_pool", f"SIM {iccid} ya está en {pool_id}")
            with self.db.tx():
                self.db.exec("UPDATE sims SET pool_id=?, last_move_day=? WHERE iccid=?", (pool_id, self.db.abs_day, iccid))
                res = {"iccid": iccid, "from_pool": sim["pool_id"], "to_pool": pool_id,
                       "moved_on_day": self.db.abs_day, "idempotent_replay": False}
                self.db.exec("INSERT INTO idem(key,iccid,pool_id,response) VALUES(?,?,?,?)",
                             (idem_key, iccid, pool_id, json.dumps(res)))
                audit(self.db, "platform", "platform.sim_moved", "sim", iccid,
                      {"from": sim["pool_id"], "to": pool_id, "caller": actor, "idem": idem_key})
            if fault:
                raise self._fault_error(fault)
            return res

    def bulk_move(self, moves, actor="api"):
        self._bulk_seq += 1
        results = []
        for i, m in enumerate(moves):
            key = m.get("idempotency_key") or f"bulk-{self._bulk_seq}-{i}"
            try:
                r = self.move_sim(m["iccid"], m["pool_id"], key, actor)
                results.append({"index": i, "iccid": m["iccid"], "ok": True, "status": 200, "result": r})
            except PlatformError as e:
                results.append({"index": i, "iccid": m["iccid"], "ok": False, "status": e.status,
                                **e.body(), "retry_after": e.retry_after})
        ok = sum(1 for r in results if r["ok"])
        return {"results": results, "summary": {"total": len(results), "ok": ok, "failed": len(results) - ok}}

    def set_status(self, iccid, status, actor="api"):
        with self.db.lock:
            fault = self._gate(iccid)
            if fault:
                raise self._fault_error(fault)
            sim = self._need_sim(iccid)
            if sim["status"] == status:
                raise PlatformError(409, "state_unchanged", f"SIM {iccid} ya está {status}")
            with self.db.tx():
                self.db.exec("UPDATE sims SET status=? WHERE iccid=?", (status, iccid))
                audit(self.db, "platform", f"platform.sim_{'suspended' if status == 'suspendida' else 'activated'}",
                      "sim", iccid, {"caller": actor})
            return {"iccid": iccid, "status": status}

    # ---------------- tráfico simulado ----------------
    def advance_day(self):
        """Genera el consumo del día actual (db.day ya incrementado). Determinista por (seed, iccid, día)."""
        abs_day = self.db.abs_day
        rows, upd = [], []
        for s in self.db.all("SELECT iccid,status,base_rate,pool_id FROM sims"):
            mb = 0.0
            if s["status"] == "activa":
                mb = s["base_rate"] * random.Random(f"{self.seed}:{s['iccid']}:{abs_day}").uniform(*NOISE)
                sp = self.spikes.get(s["iccid"])
                if sp:
                    mb *= sp["multiplier"]
            mb = round(mb, 3)
            flag = int(mb > s["base_rate"] * ANOMALY_FACTOR)
            rows.append((self.db.period, self.db.day, s["iccid"], s["pool_id"], mb))
            upd.append((mb, mb, flag, s["iccid"]))
        with self.db.tx():
            self.db.conn.executemany("INSERT INTO usage_daily(period,day,iccid,pool_id,mb) VALUES(?,?,?,?,?)", rows)
            self.db.conn.executemany(
                "UPDATE sims SET daily_mb=?, period_mb=period_mb+?, anomaly=CASE WHEN ? THEN 1 ELSE anomaly END "
                "WHERE iccid=?", upd)
        for k in list(self.spikes):
            self.spikes[k]["days_left"] -= 1
            if self.spikes[k]["days_left"] <= 0:
                del self.spikes[k]
        return round(sum(r[4] for r in rows), 3)

    def reset_period(self):
        self.db.exec("UPDATE sims SET period_mb=0, daily_mb=0, anomaly=0")
