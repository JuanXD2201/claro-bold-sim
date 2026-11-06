"""Pruebas del núcleo: semilla, reloj, plataforma simulada, motor, reportes y escenarios."""
import unittest

from orq.config import PERIOD_DAYS
from orq.platform_mock import PlatformError
from orq.reports import monthly_report, report_csv
from orq.rules import validate_rules
from orq.scenario import SCENARIOS, apply_scenario
from orq.sim import Simulation


def sim_with_rules(**rules):
    s = Simulation()
    s.orch.set_rules(rules)
    return s


def first_sim(s, pool, profile=None):
    q = "SELECT iccid FROM sims WHERE pool_id=?" + (" AND profile=?" if profile else "") + " ORDER BY iccid LIMIT 1"
    return s.db.scalar(q, (pool, profile) if profile else (pool,))


class SeedTests(unittest.TestCase):
    def test_layout(self):
        s = Simulation()
        self.assertEqual(s.db.scalar("SELECT COUNT(*) FROM sims"), 60)
        counts = {r["pool_id"]: r["n"] for r in s.db.all("SELECT pool_id, COUNT(*) n FROM sims GROUP BY pool_id")}
        self.assertEqual(counts, {"POOL-A": 20, "POOL-B": 20, "POOL-C": 20})
        a = {r["profile"]: r["n"] for r in s.db.all("SELECT profile, COUNT(*) n FROM sims WHERE pool_id='POOL-A' GROUP BY profile")}
        self.assertEqual(a, {"alto": 12, "medio": 6, "bajo": 2})
        self.assertEqual(s.db.scalar("SELECT capacity_mb FROM pools WHERE id='POOL-OVF'"), 0)

    def test_same_seed_same_data_and_usage(self):
        a, b = Simulation(), Simulation()
        a.advance_days(5)
        b.advance_days(5)
        q = "SELECT iccid, period_mb FROM sims ORDER BY iccid"
        self.assertEqual(a.db.all(q), b.db.all(q))

    def test_reset_restores_same_state(self):
        s = Simulation()
        before = s.db.all("SELECT iccid, msisdn, merchant FROM sims ORDER BY iccid")
        s.advance_days(3)
        s.reset()
        self.assertEqual(before, s.db.all("SELECT iccid, msisdn, merchant FROM sims ORDER BY iccid"))
        self.assertEqual((s.db.period, s.db.day), (1, 0))
        self.assertEqual(s.db.scalar("SELECT COUNT(*) FROM usage_daily"), 0)


class ClockTests(unittest.TestCase):
    def test_tick_and_period_end(self):
        s = Simulation()
        s.tick_day()
        self.assertEqual(s.db.day, 1)
        s.advance_to_period_end()
        self.assertEqual(s.db.day, PERIOD_DAYS)
        self.assertEqual(s.clock()["state"], "period_end")

    def test_tick_at_day_30_closes_period(self):
        s = Simulation()
        s.advance_to_period_end()
        s.tick_day()
        self.assertEqual((s.db.period, s.db.day), (2, 0))
        self.assertEqual(s.db.scalar("SELECT COALESCE(SUM(period_mb),0) FROM sims"), 0)

    def test_speed_validation(self):
        s = Simulation()
        with self.assertRaises(PlatformError):
            s.set_speed(3)
        s.set_speed(20)
        self.assertEqual(s.speed, 20)

    def test_cycle_is_measurable(self):
        s = Simulation()
        s.advance_days(3)
        self.assertEqual(len(s.metrics), 3)
        self.assertIn("cycle_ms", s.metrics[-1])


class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.s = Simulation()
        self.p = self.s.platform
        self.iccid = first_sim(self.s, "POOL-A")

    def test_move_requires_idempotency_key(self):
        with self.assertRaises(PlatformError) as c:
            self.p.move_sim(self.iccid, "POOL-C", None)
        self.assertEqual(c.exception.status, 400)

    def test_idempotent_replay_does_not_duplicate(self):
        r1 = self.p.move_sim(self.iccid, "POOL-C", "k1")
        r2 = self.p.move_sim(self.iccid, "POOL-C", "k1")
        self.assertFalse(r1["idempotent_replay"])
        self.assertTrue(r2["idempotent_replay"])
        n = self.s.db.scalar("SELECT COUNT(*) FROM audit WHERE event='platform.sim_moved' AND entity_id=?", (self.iccid,))
        self.assertEqual(n, 1)

    def test_key_reuse_with_other_body_is_409(self):
        self.p.move_sim(self.iccid, "POOL-C", "k1")
        with self.assertRaises(PlatformError) as c:
            self.p.move_sim(self.iccid, "POOL-B", "k1")
        self.assertEqual(c.exception.status, 409)

    def test_error_codes(self):
        cases = [(("0000", "POOL-C", "k"), 404), ((self.iccid, "POOL-X", "k"), 422), ((self.iccid, "POOL-A", "k"), 409)]
        for args, status in cases:
            with self.assertRaises(PlatformError) as c:
                self.p.move_sim(*args)
            self.assertEqual(c.exception.status, status)

    def test_suspended_sim_cannot_move_and_state_conflicts(self):
        self.p.set_status(self.iccid, "suspendida")
        with self.assertRaises(PlatformError) as c:
            self.p.move_sim(self.iccid, "POOL-C", "k")
        self.assertEqual(c.exception.status, 409)
        with self.assertRaises(PlatformError):
            self.p.set_status(self.iccid, "suspendida")
        self.p.set_status(self.iccid, "activa")

    def test_rate_limit_429_with_retry_after_and_reset_each_cycle(self):
        self.p.set_rate_limit(True, 2, 7)
        self.p.get_sim(self.iccid)
        self.p.get_sim(self.iccid)
        with self.assertRaises(PlatformError) as c:
            self.p.get_sim(self.iccid)
        self.assertEqual((c.exception.status, c.exception.retry_after), (429, 7))
        self.p.new_cycle()
        self.p.get_sim(self.iccid)

    def test_transient_fault_expires(self):
        self.p.add_fault("global", kind="transient", status=503, count=2)
        for _ in range(2):
            with self.assertRaises(PlatformError) as c:
                self.p.get_sim(self.iccid)
            self.assertEqual(c.exception.status, 503)
        self.p.get_sim(self.iccid)

    def test_permanent_fault_per_sim_only_affects_that_sim(self):
        other = first_sim(self.s, "POOL-B")
        self.p.add_fault("sim", iccid=self.iccid, kind="permanent")
        for _ in range(3):
            with self.assertRaises(PlatformError) as c:
                self.p.get_sim(self.iccid)
            self.assertEqual(c.exception.status, 500)
        self.p.get_sim(other)

    def test_bulk_move_partial_result(self):
        a2 = self.s.db.all("SELECT iccid FROM sims WHERE pool_id='POOL-A' ORDER BY iccid LIMIT 2")[1]["iccid"]
        res = self.p.bulk_move([{"iccid": self.iccid, "pool_id": "POOL-C"}, {"iccid": "nope", "pool_id": "POOL-C"},
                                {"iccid": a2, "pool_id": "POOL-Z"}])
        self.assertEqual([r["status"] for r in res["results"]], [200, 404, 422])
        self.assertEqual(res["summary"], {"total": 3, "ok": 1, "failed": 2})

    def test_bulk_move_hits_rate_limit_midway(self):
        sims = [r["iccid"] for r in self.s.db.all("SELECT iccid FROM sims WHERE pool_id='POOL-A' LIMIT 4")]
        self.p.set_rate_limit(True, 2, 3)
        res = self.p.bulk_move([{"iccid": i, "pool_id": "POOL-C"} for i in sims])
        self.assertEqual([r["status"] for r in res["results"]], [200, 200, 429, 429])


class EngineTests(unittest.TestCase):
    def test_rules_validation(self):
        with self.assertRaises(PlatformError) as c:
            validate_rules({"warn_threshold": 1.2, "critical_threshold": 1.0})
        self.assertEqual(c.exception.status, 422)
        with self.assertRaises(PlatformError):
            validate_rules({"mode": "x"})
        self.assertEqual(validate_rules({})["mode"], "supervised")

    def test_supervised_proposes_and_does_not_move(self):
        s = sim_with_rules(min_history_days=3)
        s.advance_days(4)
        proposed = s.orch.list_moves(state="proposed")
        self.assertGreater(len(proposed), 0)
        self.assertTrue(all(m["from_pool"] == "POOL-A" for m in proposed))
        self.assertEqual(s.db.scalar("SELECT COUNT(*) FROM sims WHERE pool_id='POOL-A'"), 20)

    def test_no_projection_before_min_history(self):
        s = sim_with_rules(min_history_days=10)
        s.advance_days(5)
        self.assertEqual(s.orch.list_moves(), [])
        self.assertIsNotNone(s.orch.preview()["note"])

    def test_real_trigger_waits_for_actual_usage(self):
        s = sim_with_rules(trigger="real")
        s.advance_days(10)
        self.assertEqual(s.orch.list_moves(), [])       # A solo lleva ~430 MB (54%)

    def test_approve_executes_and_moves_sim(self):
        s = sim_with_rules(min_history_days=3)
        s.advance_days(4)
        m = s.orch.list_moves(state="proposed")[0]
        r = s.orch.approve([m["id"]])[0]
        self.assertEqual(r["move"]["state"], "executed")
        self.assertEqual(s.db.scalar("SELECT pool_id FROM sims WHERE iccid=?", (m["iccid"],)), m["to_pool"])
        self.assertEqual(r["api"]["status"], 200)

    def test_reject_and_state_conflicts(self):
        s = sim_with_rules(min_history_days=3)
        s.advance_days(4)
        mid = s.orch.list_moves(state="proposed")[0]["id"]
        self.assertEqual(s.orch.reject([mid])[0]["move"]["state"], "rejected")
        self.assertIn("error", s.orch.reject([mid])[0])
        self.assertIn("error", s.orch.approve([mid])[0])

    def test_automatic_mode_executes_without_approval(self):
        s = sim_with_rules(min_history_days=3, mode="automatic")
        s.advance_days(4)
        self.assertGreater(len(s.orch.list_moves(state="executed")), 0)
        self.assertEqual(s.orch.list_moves(state="proposed"), [])

    def test_preview_does_not_persist(self):
        s = sim_with_rules(min_history_days=3)
        s.advance_days(4)
        n = s.db.scalar("SELECT COUNT(*) FROM moves")
        p = s.orch.preview({"warn_threshold": 0.5, "critical_threshold": 0.6})
        self.assertGreaterEqual(len(p["proposals"]), 1)
        self.assertEqual(s.db.scalar("SELECT COUNT(*) FROM moves"), n)

    def test_transient_failure_is_retried_with_same_idempotency_key(self):
        s = sim_with_rules(min_history_days=3)
        s.advance_days(4)
        s.platform.add_fault("global", kind="transient", status=503, count=2)
        m = s.orch.list_moves(state="proposed")[0]
        r = s.orch.approve([m["id"]])[0]
        self.assertEqual(r["move"]["state"], "executed")
        self.assertEqual(r["move"]["attempts"], 3)
        self.assertEqual(s.db.scalar("SELECT COUNT(*) FROM alerts WHERE type='api_failure' AND severity='info'"), 1)

    def test_permanent_failure_marks_failed_and_alerts_then_retry(self):
        s = sim_with_rules(min_history_days=3)
        s.advance_days(4)
        m = s.orch.list_moves(state="proposed")[0]
        s.platform.add_fault("sim", iccid=m["iccid"], kind="permanent", status=500)
        r = s.orch.approve([m["id"]])[0]
        self.assertEqual(r["move"]["state"], "failed")
        self.assertEqual(s.db.scalar("SELECT COUNT(*) FROM alerts WHERE type='api_failure' AND severity='critical'"), 1)
        s.platform.clear_faults()
        r2 = s.orch.retry([m["id"]])[0]
        self.assertEqual(r2["move"]["state"], "executed")

    def test_rate_limit_defers_and_next_cycle_completes(self):
        s = sim_with_rules(min_history_days=3, mode="automatic")
        s.platform.set_rate_limit(True, 2, 5)
        s.advance_days(3)
        deferred = s.orch.list_moves(state="approved")
        self.assertGreater(len(deferred), 0)
        self.assertEqual(s.db.scalar("SELECT COUNT(*) FROM alerts WHERE type='rate_limit'"), 1)
        s.platform.set_rate_limit(False, 0, 1)
        s.tick_day()
        self.assertEqual(s.orch.list_moves(state="approved"), [])

    def test_revert_single_and_batch(self):
        s = sim_with_rules(min_history_days=3, mode="automatic")
        s.advance_days(3)
        done = s.orch.list_moves(state="executed")
        self.assertGreater(len(done), 1)
        one = done[0]
        r = s.orch.revert(one["id"])
        self.assertEqual(r["move"]["state"], "executed")
        self.assertEqual(s.db.scalar("SELECT pool_id FROM sims WHERE iccid=?", (one["iccid"],)), one["from_pool"])
        self.assertEqual(s.orch.get_move(one["id"])["state"], "reverted")
        with self.assertRaises(PlatformError):
            s.orch.revert(one["id"])
        batch = done[1]["batch_id"]
        res = s.orch.revert_batch(batch)
        self.assertTrue(all(x.get("move", {}).get("state") == "executed" for x in res["results"]))
        self.assertEqual(s.orch.reconcile(), [])

    def test_reconcile_heals_move_applied_but_response_lost(self):
        s = sim_with_rules(min_history_days=3)
        s.advance_days(4)
        m = s.orch.list_moves(state="proposed")[0]
        s.platform.add_fault("sim", iccid=m["iccid"], kind="permanent", status=503, count=None, commit_then_fail=True)
        r = s.orch.approve([m["id"]])[0]
        self.assertEqual(r["move"]["state"], "failed")                       # el orquestador no vio el éxito
        self.assertEqual(s.db.scalar("SELECT pool_id FROM sims WHERE iccid=?", (m["iccid"],)), m["to_pool"])  # pero se aplicó
        self.assertEqual(s.db.scalar("SELECT COUNT(*) FROM audit WHERE event='platform.sim_moved' AND entity_id=?", (m["iccid"],)), 1)
        fixed = s.orch.reconcile()
        self.assertEqual(fixed[0]["action"], "healed")
        self.assertEqual(s.orch.get_move(m["id"])["state"], "executed")

    def test_reconcile_adopts_external_change(self):
        s = Simulation()
        iccid = first_sim(s, "POOL-A")
        s.platform.move_sim(iccid, "POOL-C", "ext-1")
        fixed = s.orch.reconcile()
        self.assertEqual(fixed[0]["action"], "adopted")
        self.assertEqual(s.orch.reconcile(), [])
        self.assertEqual(s.db.scalar("SELECT COUNT(*) FROM alerts WHERE type='conciliacion'"), 1)

    def test_manual_move_validations(self):
        s = Simulation()
        iccid = first_sim(s, "POOL-A")
        r = s.orch.manual_move(iccid, "POOL-C")
        self.assertEqual(r["move"]["state"], "executed")
        for args, status in (((iccid, "POOL-C"), 409), (("x", "POOL-C"), 404), ((first_sim(s, "POOL-B"), "NOPE"), 422)):
            with self.assertRaises(PlatformError) as c:
                s.orch.manual_move(*args)
            self.assertEqual(c.exception.status, status)

    def test_anomaly_alert(self):
        s = Simulation()
        iccid = first_sim(s, "POOL-C")
        s.platform.inject_spike(iccid, 15, 1)
        s.tick_day()
        self.assertEqual(s.db.scalar("SELECT COUNT(*) FROM alerts WHERE type='anomalia' AND subject_id=?", (iccid,)), 1)
        self.assertEqual(s.db.scalar("SELECT anomaly FROM sims WHERE iccid=?", (iccid,)), 1)


class PeriodAndReportTests(unittest.TestCase):
    def test_r4_return_from_overflow_on_close(self):
        s = Simulation()
        iccid = first_sim(s, "POOL-A")
        s.orch.manual_move(iccid, "POOL-OVF")
        s.advance_days(2)
        s.close_period()
        self.assertEqual(s.db.scalar("SELECT pool_id FROM sims WHERE iccid=?", (iccid,)), "POOL-A")
        self.assertEqual(s.orch.list_moves(state="executed")[0]["kind"], "return")

    def test_overflow_usage_is_all_excess_and_totals_match(self):
        s = Simulation()
        s.orch.manual_move(first_sim(s, "POOL-A", "alto"), "POOL-OVF")
        s.advance_to_period_end()
        r = monthly_report(s.db)
        ovf = next(p for p in r["pools"] if p["pool_id"] == "POOL-OVF")
        self.assertGreater(ovf["used_mb"], 0)
        self.assertEqual(ovf["excess_mb"], ovf["used_mb"])
        self.assertAlmostEqual(r["totals"]["used_mb"], sum(p["used_mb"] for p in r["pools"]), places=2)
        self.assertAlmostEqual(r["totals"]["used_mb"], s.db.scalar("SELECT SUM(period_mb) FROM sims"), delta=0.05)
        self.assertTrue(r["reconciled"])

    def test_report_survives_period_close_and_csv_total(self):
        s = Simulation()
        s.advance_to_period_end()
        before = monthly_report(s.db, 1)["totals"]["used_mb"]
        s.close_period()
        r1 = monthly_report(s.db, 1)
        self.assertTrue(r1["closed"])
        self.assertEqual(r1["totals"]["used_mb"], before)
        csv_text = report_csv(r1)
        total_row = [l for l in csv_text.splitlines() if ",TOTAL," in l][0].split(",")
        self.assertAlmostEqual(float(total_row[3]), before, places=2)
        self.assertEqual(monthly_report(s.db)["totals"]["used_mb"], 0)


class ScenarioTests(unittest.TestCase):
    def test_all_scenarios_apply_and_pause(self):
        s = Simulation()
        for key in SCENARIOS:
            res = apply_scenario(s, key)
            self.assertEqual(res["scenario"], key)
            self.assertFalse(s.running)

    def test_pool_a_saturates_has_pending_proposals_and_alerts(self):
        s = Simulation()
        apply_scenario(s, "pool_a_saturates")
        self.assertGreater(s.orch.summary()["pending_moves"], 0)
        self.assertGreater(len(s.orch.list_alerts(type_="umbral")), 0)

    def test_api_failures_scenario_shows_retry_and_failure(self):
        s = Simulation()
        apply_scenario(s, "api_failures")
        sev = {a["severity"] for a in s.orch.list_alerts(type_="api_failure")}
        self.assertEqual(sev, {"info", "critical"})
        self.assertGreater(len(s.orch.list_moves(state="failed")), 0)

    def test_rate_limit_scenario(self):
        s = Simulation()
        apply_scenario(s, "rate_limit")
        self.assertGreater(len(s.orch.list_alerts(type_="rate_limit")), 0)

    def test_anomaly_scenario(self):
        s = Simulation()
        apply_scenario(s, "anomaly_spike")
        self.assertEqual(len(s.orch.list_alerts(type_="anomalia")), 1)

    def test_overflow_scenario_ends_at_day_30(self):
        s = Simulation()
        apply_scenario(s, "overflow_close")
        self.assertEqual(s.db.day, PERIOD_DAYS)
        self.assertTrue(monthly_report(s.db)["reconciled"])


if __name__ == "__main__":
    unittest.main()
