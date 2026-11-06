"""E2E ligeras: levantan el servidor real (uvicorn, 127.0.0.1) con una BD temporal y recorren los flujos por HTTP.
Para pruebas con navegador (Playwright) ver README: no se instala por defecto (peso)."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class E2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.port = free_port()
        code = ("import uvicorn,sys; from orq.api.app import create_app; "
                f"uvicorn.run(create_app(db_path=r'{cls.tmp.name}/e2e.db'), host='127.0.0.1', port={cls.port}, log_level='error')")
        cls.proc = subprocess.Popen([sys.executable, "-c", code], cwd=ROOT)
        for _ in range(100):
            try:
                cls.call("GET", "/healthz")
                return
            except Exception:
                time.sleep(0.2)
        cls.proc.kill()
        raise RuntimeError("el servidor no arrancó")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(10)
        try:
            cls.tmp.cleanup()
        except OSError:
            pass          # Windows puede retener el archivo SQLite un instante

    @classmethod
    def call(cls, method, path, body=None, headers=None):
        req = urllib.request.Request(f"http://127.0.0.1:{cls.port}{path}", method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json", "X-Client": "ui", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    def test_1_spa_and_docs_are_served(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/") as r:
            self.assertIn(b"Simulador de pools", r.read())
        self.assertEqual(self.call("GET", "/openapi.json")[0], 200)

    def test_2_filter_sims(self):
        st, d = self.call("GET", "/platform/sims?pool_id=POOL-A&profile=alto&status=activa&sort=iccid")
        self.assertEqual((st, d["total"]), (200, 12))
        st, d = self.call("GET", "/platform/sims?anomaly=false&pending_move=false&min_period=0&page_size=10")
        self.assertEqual((d["total"], len(d["items"])), (60, 10))

    def test_3_move_sim_via_orchestrator_and_api_log(self):
        iccid = self.call("GET", "/platform/sims?pool_id=POOL-A&page_size=1")[1]["items"][0]["iccid"]
        st, r = self.call("POST", "/orchestrator/moves", {"iccid": iccid, "pool_id": "POOL-C"})
        self.assertEqual((st, r["move"]["state"]), (200, "executed"))
        self.assertEqual(self.call("GET", f"/platform/sims/{iccid}")[1]["pool_id"], "POOL-C")
        log = self.call("GET", "/sim/api-log")[1]["items"]
        self.assertTrue(any(e["path"].endswith("/move") and e["status"] == 200 and e["source"] == "orchestrator" for e in log))

    def test_4_scenario_then_approve_proposal(self):
        self.assertEqual(self.call("POST", "/sim/scenarios/pool_a_saturates")[0], 200)
        moves = self.call("GET", "/orchestrator/moves?state=proposed")[1]["items"]
        self.assertGreater(len(moves), 0)
        st, r = self.call("POST", f"/orchestrator/moves/{moves[0]['id']}/approve")
        self.assertEqual((st, r["move"]["state"]), (200, "executed"))
        pools = {p["id"]: p for p in self.call("GET", "/orchestrator/pools")[1]["items"]}
        self.assertEqual(pools["POOL-A"]["sim_count"], 19)


if __name__ == "__main__":
    unittest.main()
