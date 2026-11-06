"""Pruebas de la API HTTP (TestClient, sin red): idempotencia, 429, fallas, bulk parcial, conciliación, reportes."""
import csv
import io
import unittest

from fastapi.testclient import TestClient

from orq.api.app import create_app
from orq.sim import Simulation

EXT = {"X-Client": "tester"}


class ApiBase(unittest.TestCase):
    def setUp(self):
        self.sim = Simulation()
        self.c = TestClient(create_app(self.sim, run_clock=False), headers=EXT)
        self.a_sims = [r["iccid"] for r in self.sim.db.all("SELECT iccid FROM sims WHERE pool_id='POOL-A' ORDER BY iccid")]

    def assertError(self, r, status, code=None):
        self.assertEqual(r.status_code, status, r.text)
        body = r.json()
        self.assertEqual(set(body), {"error"})
        self.assertEqual(body["error"]["status"], status)
        if code:
            self.assertEqual(body["error"]["code"], code)


class PlatformApiTests(ApiBase):
    def test_pools_and_sims_listing_with_filters(self):
        pools = self.c.get("/platform/pools").json()["items"]
        self.assertEqual([p["id"] for p in pools], ["POOL-A", "POOL-B", "POOL-C", "POOL-OVF"])
        r = self.c.get("/platform/sims", params={"pool_id": "POOL-A", "profile": "alto", "page_size": 5}).json()
        self.assertEqual((r["total"], len(r["items"])), (12, 5))
        self.assertEqual(self.c.get("/platform/sims", params={"q": self.a_sims[0][-6:]}).json()["total"], 1)

    def test_move_requires_idempotency_key_400(self):
        r = self.c.post(f"/platform/sims/{self.a_sims[0]}/move", json={"pool_id": "POOL-C"})
        self.assertError(r, 400, "missing_idempotency_key")

    def test_move_idempotent_replay_header_and_single_effect(self):
        url, h = f"/platform/sims/{self.a_sims[0]}/move", {"Idempotency-Key": "abc"}
        r1 = self.c.post(url, json={"pool_id": "POOL-C"}, headers=h)
        r2 = self.c.post(url, json={"pool_id": "POOL-C"}, headers=h)
        self.assertEqual((r1.status_code, r2.status_code), (200, 200))
        self.assertNotIn("idempotent-replay", r1.headers)
        self.assertEqual(r2.headers["idempotent-replay"], "true")
        self.assertEqual(self.sim.db.scalar("SELECT COUNT(*) FROM audit WHERE event='platform.sim_moved'"), 1)
        r3 = self.c.post(url, json={"pool_id": "POOL-B"}, headers=h)
        self.assertError(r3, 409, "idempotency_key_reused")

    def test_error_codes_are_uniform(self):
        h = {"Idempotency-Key": "k"}
        self.assertError(self.c.get("/platform/sims/no-existe"), 404, "sim_not_found")
        self.assertError(self.c.post(f"/platform/sims/{self.a_sims[0]}/move", json={"pool_id": "POOL-ZZ"}, headers=h), 422, "unknown_pool")
        self.assertError(self.c.post(f"/platform/sims/{self.a_sims[0]}/move", json={"pool_id": "POOL-A"}, headers=h), 409, "already_in_pool")
        self.assertError(self.c.post(f"/platform/sims/{self.a_sims[0]}/move", json={"pool_id": 5}, headers=h), 422, "validation_error")
        self.assertError(self.c.post(f"/platform/sims/{self.a_sims[0]}/move", json={"pool_id": "POOL-C", "x": 1}, headers=h), 422)
        self.assertError(self.c.get("/platform/ruta-inexistente"), 404, "not_found")
        self.assertError(self.c.get("/platform/sims", params={"page_size": 0}), 422)

    def test_rate_limit_429_retry_after_and_cycle_reset(self):
        self.c.put("/sim/rate-limit", json={"enabled": True, "max_calls": 2, "retry_after_s": 9})
        self.c.get("/platform/pools")
        self.c.get("/platform/pools")
        r = self.c.get("/platform/pools")
        self.assertError(r, 429, "rate_limited")
        self.assertEqual(r.headers["retry-after"], "9")
        self.c.post("/sim/tick")                          # nuevo ciclo
        self.assertEqual(self.c.get("/platform/pools").status_code, 200)

    def test_ui_reads_do_not_consume_limit_or_fail(self):
        self.c.put("/sim/rate-limit", json={"enabled": True, "max_calls": 1, "retry_after_s": 1})
        self.c.post("/sim/faults", json={"scope": "global", "kind": "permanent"})
        for _ in range(3):
            self.assertEqual(self.c.get("/platform/sims", headers={"X-Client": "ui"}).status_code, 200)
        self.assertEqual(len([e for e in self.sim.apilog.list() if e["source"] == "ui"]), 0)

    def test_transient_and_permanent_faults(self):
        self.c.post("/sim/faults", json={"scope": "global", "kind": "transient", "status": 503, "count": 2})
        self.assertError(self.c.get("/platform/pools"), 503, "transient_failure")
        self.assertError(self.c.get("/platform/pools"), 503)
        self.assertEqual(self.c.get("/platform/pools").status_code, 200)
        self.c.post("/sim/faults", json={"scope": "sim", "iccid": self.a_sims[0], "kind": "permanent"})
        self.assertError(self.c.get(f"/platform/sims/{self.a_sims[0]}"), 500, "permanent_failure")
        self.assertEqual(self.c.get(f"/platform/sims/{self.a_sims[1]}").status_code, 200)
        self.c.delete("/sim/faults")
        self.assertEqual(self.c.get(f"/platform/sims/{self.a_sims[0]}").status_code, 200)

    def test_bulk_move_partial(self):
        body = {"moves": [{"iccid": self.a_sims[0], "pool_id": "POOL-C"}, {"iccid": "nada", "pool_id": "POOL-C"},
                          {"iccid": self.a_sims[1], "pool_id": "POOL-A"}, {"iccid": self.a_sims[2], "pool_id": "POOL-B"}]}
        r = self.c.post("/platform/sims/bulk-move", json=body)
        self.assertEqual(r.status_code, 200)
        out = r.json()
        self.assertEqual([x["status"] for x in out["results"]], [200, 404, 409, 200])
        self.assertEqual(out["summary"], {"total": 4, "ok": 2, "failed": 2})
        self.assertEqual(self.sim.db.scalar("SELECT pool_id FROM sims WHERE iccid=?", (self.a_sims[2],)), "POOL-B")

    def test_suspend_activate(self):
        i = self.a_sims[0]
        self.assertEqual(self.c.post(f"/platform/sims/{i}/suspend").json()["status"], "suspendida")
        self.assertError(self.c.post(f"/platform/sims/{i}/suspend"), 409)
        self.assertError(self.c.post(f"/platform/sims/{i}/move", json={"pool_id": "POOL-C"}, headers={"Idempotency-Key": "z"}), 409, "sim_suspended")
        self.assertEqual(self.c.post(f"/platform/sims/{i}/activate").status_code, 200)

    def test_api_log_records_calls_with_latency_and_status(self):
        self.c.get("/platform/pools")
        self.c.get("/platform/sims/zzz")
        log = self.c.get("/sim/api-log").json()["items"]
        self.assertEqual([(e["path"], e["status"]) for e in log[-2:]], [("/platform/pools", 200), ("/platform/sims/zzz", 404)])
        self.assertIn("latency_ms", log[-1])

    def test_openapi_documents_both_prefixes(self):
        paths = self.c.get("/openapi.json").json()["paths"]
        for p in ("/platform/pools", "/platform/sims/{iccid}/move", "/platform/sims/bulk-move", "/orchestrator/rules",
                  "/orchestrator/reports/monthly"):
            self.assertIn(p, paths)


class OrchestratorApiTests(ApiBase):
    def setUp(self):
        super().setUp()
        self.c.put("/orchestrator/rules", json={"min_history_days": 3})
        for _ in range(4):
            self.c.post("/sim/tick")

    def test_rules_get_put_validation_and_preview(self):
        r = self.c.put("/orchestrator/rules", json={"mode": "automatic"}).json()
        self.assertEqual(r["mode"], "automatic")
        self.assertError(self.c.put("/orchestrator/rules", json={"warn_threshold": 5}), 422)
        self.assertError(self.c.put("/orchestrator/rules", json={"warn_threshold": 0.9, "critical_threshold": 0.5}), 422, "invalid_rules")
        n = len(self.c.get("/orchestrator/moves").json()["items"])
        p = self.c.post("/orchestrator/rules/preview", json={"warn_threshold": 0.3, "critical_threshold": 0.4}).json()
        self.assertGreater(len(p["proposals"]), 0)
        self.assertEqual(len(self.c.get("/orchestrator/moves").json()["items"]), n)

    def test_supervised_flow_approve_reject_bulk_retry_revert(self):
        proposed = self.c.get("/orchestrator/moves", params={"state": "proposed"}).json()["items"]
        self.assertGreaterEqual(len(proposed), 3)
        ok = self.c.post(f"/orchestrator/moves/{proposed[0]['id']}/approve").json()
        self.assertEqual(ok["move"]["state"], "executed")
        self.assertEqual(ok["api"]["status"], 200)
        self.assertError(self.c.post(f"/orchestrator/moves/{proposed[0]['id']}/approve"), 409, "invalid_state")
        self.assertEqual(self.c.post(f"/orchestrator/moves/{proposed[1]['id']}/reject").json()["move"]["state"], "rejected")
        self.c.post("/sim/faults", json={"scope": "sim", "iccid": proposed[2]["iccid"], "kind": "permanent"})
        bulk = self.c.post("/orchestrator/moves/approve", json={"ids": [proposed[2]["id"], 99999]}).json()["results"]
        self.assertEqual(bulk[0]["move"]["state"], "failed")
        self.assertEqual(bulk[1]["error"]["status"], 404)
        self.c.delete("/sim/faults")
        self.assertEqual(self.c.post(f"/orchestrator/moves/{proposed[2]['id']}/retry").json()["move"]["state"], "executed")
        rv = self.c.post(f"/orchestrator/moves/{proposed[0]['id']}/revert").json()
        self.assertEqual(rv["move"]["state"], "executed")
        self.assertEqual(self.c.get(f"/orchestrator/moves/{proposed[0]['id']}").json()["state"], "reverted")

    def test_manual_move_from_orchestrator_registers_api_call_and_audit(self):
        self.c.delete("/sim/api-log")
        c_sim = self.sim.db.scalar("SELECT iccid FROM sims WHERE pool_id='POOL-C' ORDER BY iccid LIMIT 1")
        r = self.c.post("/orchestrator/moves", json={"iccid": c_sim, "pool_id": "POOL-B"}).json()
        self.assertEqual(r["move"]["state"], "executed")
        log = [e for e in self.c.get("/sim/api-log").json()["items"] if e["source"] == "orchestrator"]
        self.assertEqual(log[-1]["status"], 200)
        self.assertIn("latency_ms", log[-1])
        ev = self.c.get("/orchestrator/audit", params={"event": "move."}).json()["items"]
        self.assertTrue(any(e["actor"] == "user" for e in ev))

    def test_conciliation_after_direct_platform_move(self):
        self.c.post(f"/platform/sims/{self.a_sims[5]}/move", json={"pool_id": "POOL-B"}, headers={"Idempotency-Key": "ext"})
        self.c.post("/sim/tick")                           # el ciclo concilia
        al = self.c.get("/orchestrator/alerts", params={"type": "conciliacion"}).json()["items"]
        self.assertEqual(len(al), 1)

    def test_alerts_ack_resolve(self):
        a = self.c.get("/orchestrator/alerts", params={"state": "open"}).json()["items"]
        self.assertGreater(len(a), 0)
        r = self.c.post("/orchestrator/alerts/acknowledge", json={"ids": [a[0]["id"]]}).json()["results"][0]
        self.assertEqual(r["alert"]["state"], "acknowledged")
        r = self.c.post("/orchestrator/alerts/resolve", json={"ids": [a[0]["id"], 9999]}).json()["results"]
        self.assertEqual(r[0]["alert"]["state"], "resolved")
        self.assertEqual(r[1]["error"]["status"], 404)

    def test_sim_detail(self):
        d = self.c.get(f"/orchestrator/sims/{self.a_sims[0]}/detail").json()
        self.assertEqual(len(d["usage"]["rows"]), 4)
        self.assertIn("audit", d)

    def test_monthly_report_json_csv_totals_and_audit_csv(self):
        self.c.post("/orchestrator/moves", json={"iccid": self.a_sims[0], "pool_id": "POOL-OVF"})
        self.c.post("/sim/advance-to-period-end")
        rep = self.c.get("/orchestrator/reports/monthly").json()
        self.assertTrue(rep["reconciled"])
        self.assertAlmostEqual(rep["totals"]["used_mb"], sum(p["used_mb"] for p in rep["pools"]), places=2)
        rows = list(csv.DictReader(io.StringIO(self.c.get("/orchestrator/reports/monthly", params={"format": "csv"}).text)))
        total = next(r for r in rows if r["pool_id"] == "TOTAL")
        self.assertAlmostEqual(float(total["used_mb"]), sum(float(r["used_mb"]) for r in rows if r["pool_id"] != "TOTAL"), places=2)
        self.assertAlmostEqual(float(total["used_mb"]), rep["totals"]["used_mb"], places=2)
        self.assertError(self.c.get("/orchestrator/reports/monthly", params={"period": 9}), 404)
        acsv = self.c.get("/orchestrator/audit.csv", params={"actor": "user"})
        self.assertIn("text/csv", acsv.headers["content-type"])
        self.assertGreater(len(list(csv.reader(io.StringIO(acsv.text)))), 1)

    def test_close_period_returns_overflow_sim(self):
        self.c.post("/orchestrator/moves", json={"iccid": self.a_sims[0], "pool_id": "POOL-OVF"})
        self.c.post("/sim/advance-to-period-end")
        self.c.post("/sim/close-period")
        self.assertEqual(self.c.get(f"/platform/sims/{self.a_sims[0]}").json()["pool_id"], "POOL-A")
        self.assertEqual(self.c.get("/sim/state").json()["clock"]["period"], 2)


class ControlApiTests(ApiBase):
    def test_scenarios_and_reset(self):
        keys = [s["key"] for s in self.c.get("/sim/scenarios").json()["items"]]
        self.assertEqual(len(keys), 6)
        r = self.c.post("/sim/scenarios/pool_a_saturates").json()
        self.assertEqual(r["clock"]["day"], 4)
        self.assertError(self.c.post("/sim/scenarios/nope"), 404, "scenario_not_found")
        self.assertEqual(self.c.post("/sim/reset").json()["day"], 0)

    def test_play_pause_speed(self):
        self.assertTrue(self.c.post("/sim/play").json()["running"])
        self.assertEqual(self.c.post("/sim/speed", json={"speed": 20}).json()["speed"], 20)
        self.assertError(self.c.post("/sim/speed", json={"speed": 7}), 422)
        self.assertFalse(self.c.post("/sim/pause").json()["running"])

    def test_host_header_rejected_and_cors(self):
        self.assertEqual(self.c.get("/healthz", headers={"Host": "evil.example"}).status_code, 400)
        r = self.c.get("/healthz", headers={"Origin": "http://evil.example"})
        self.assertNotIn("access-control-allow-origin", r.headers)


if __name__ == "__main__":
    unittest.main()
