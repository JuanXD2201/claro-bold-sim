"""Reporte mensual consolidado (JSON/CSV). Los totales salen de usage_daily, igual que cada pool."""
import csv
import io


def monthly_report(db, period=None):
    period = period or db.period
    used = {r["pool_id"]: r["mb"] for r in db.all(
        "SELECT pool_id, SUM(mb) AS mb FROM usage_daily WHERE period=? GROUP BY pool_id", (period,))}
    sims_now = {r["pool_id"]: r["n"] for r in db.all("SELECT pool_id, COUNT(*) AS n FROM sims GROUP BY pool_id")}
    ins = {r["to_pool"]: r["n"] for r in db.all(
        "SELECT to_pool, COUNT(*) AS n FROM moves WHERE period=? AND state IN ('executed') GROUP BY to_pool", (period,))}
    outs = {r["from_pool"]: r["n"] for r in db.all(
        "SELECT from_pool, COUNT(*) AS n FROM moves WHERE period=? AND state IN ('executed') GROUP BY from_pool", (period,))}
    rows = []
    for p in db.all("SELECT * FROM pools ORDER BY is_overflow, id"):
        u = round(used.get(p["id"], 0.0), 3)
        cap = p["capacity_mb"]
        rows.append({"pool_id": p["id"], "capacity_mb": cap, "used_mb": u,
                     "excess_mb": round(max(0.0, u - cap), 3),
                     "utilization_pct": round(100 * u / cap, 1) if cap else None,
                     "sims_current": sims_now.get(p["id"], 0),
                     "moves_in": ins.get(p["id"], 0), "moves_out": outs.get(p["id"], 0)})
    totals = {"capacity_mb": round(sum(r["capacity_mb"] for r in rows), 3),
              "used_mb": round(sum(r["used_mb"] for r in rows), 3),
              "excess_mb": round(sum(r["excess_mb"] for r in rows), 3),
              "sims_current": sum(r["sims_current"] for r in rows),
              "moves_in": sum(r["moves_in"] for r in rows), "moves_out": sum(r["moves_out"] for r in rows)}
    direct = round(db.scalar("SELECT COALESCE(SUM(mb),0) FROM usage_daily WHERE period=?", (period,)), 3)
    return {"period": period, "closed": period < db.period, "days_elapsed": 30 if period < db.period else db.day,
            "pools": rows, "totals": totals, "reconciled": abs(direct - totals["used_mb"]) < 0.01,
            "direct_total_mb": direct}


def report_csv(report):
    buf = io.StringIO()
    w = csv.writer(buf)
    cols = ["period", "pool_id", "capacity_mb", "used_mb", "excess_mb", "utilization_pct", "sims_current",
            "moves_in", "moves_out"]
    w.writerow(cols)
    for r in report["pools"]:
        w.writerow([report["period"]] + [r[c] for c in cols[1:]])
    t = report["totals"]
    w.writerow([report["period"], "TOTAL", t["capacity_mb"], t["used_mb"], t["excess_mb"], "", t["sims_current"],
                t["moves_in"], t["moves_out"]])
    return buf.getvalue()
