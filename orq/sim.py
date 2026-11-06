"""Fachada de la simulación: reloj simulado, ciclo diario, cierre de periodo y reinicio."""
import time
from collections import deque

from .apilog import ApiLog
from .config import BASE_TICK_S, PERIOD_DAYS, SEED, SPEEDS
from .db import Database
from .engine import Orchestrator
from .platform_mock import PlatformError, PlatformMock
from .records import audit
from .seed import seed_database


class Simulation:
    def __init__(self, db_path=":memory:", seed=SEED):
        self.seed = seed
        self.db = Database(db_path)
        self.apilog = ApiLog()
        self.platform = PlatformMock(self.db, seed)
        self.orch = Orchestrator(self.db, self.platform, self.apilog)
        self.running, self.speed, self.version = False, 1, 0
        self.metrics = deque(maxlen=300)
        self.scenario = None
        self._last_tick = time.monotonic()
        if self.db.scalar("SELECT COUNT(*) FROM pools"):
            c = self.db.kv_get("clock", {"period": 1, "day": 0})
            self.db.period, self.db.day = c["period"], c["day"]
        else:
            self._init_data()

    # --------------------------------------------------------- estado
    def _init_data(self):
        seed_database(self.db, self.seed)
        self.db.period, self.db.day = 1, 0
        self._save_clock()
        audit(self.db, "system", "sim.initialized", "sim", None, {"seed": self.seed})

    def _save_clock(self):
        self.db.kv_set("clock", {"period": self.db.period, "day": self.db.day})

    def bump(self):
        self.version += 1

    def clock(self):
        d = self.db
        state = "running" if self.running else ("period_end" if d.day >= PERIOD_DAYS else "paused")
        return {"period": d.period, "day": d.day, "abs_day": d.abs_day, "period_days": PERIOD_DAYS,
                "running": self.running, "speed": self.speed, "state": state, "version": self.version,
                "scenario": self.scenario}

    # --------------------------------------------------------- control
    def play(self):
        self.running = True
        self._last_tick = time.monotonic()
        self.bump()

    def pause(self):
        self.running = False
        self.bump()

    def set_speed(self, speed):
        if speed not in SPEEDS:
            raise PlatformError(422, "invalid_speed", f"Velocidad debe ser una de {SPEEDS}")
        self.speed = speed
        self.bump()

    def due(self):
        """True si el reloj en tiempo real debe avanzar un día simulado."""
        return self.running and time.monotonic() - self._last_tick >= BASE_TICK_S / self.speed

    def tick_day(self):
        t0 = time.perf_counter()
        with self.db.lock:
            self._last_tick = time.monotonic()
            if self.db.day >= PERIOD_DAYS:
                self.close_period()
                return {"closed_period": True}
            self.db.day += 1
            try:
                self.platform.new_cycle()
                with self.db.tx():
                    usage = self.platform.advance_day()
                    stats = self.orch.cycle()
                    self._save_clock()
            except BaseException:
                self.db.day -= 1
                raise
            if self.db.day >= PERIOD_DAYS:
                self.running = False          # fin de periodo: el usuario decide cerrar
            ms = round((time.perf_counter() - t0) * 1000, 2)
            m = {"period": self.db.period, "day": self.db.day, "cycle_ms": ms, "usage_mb": usage,
                 "api_calls": self.platform.calls_in_cycle, **stats}
            self.metrics.append(m)
            self.bump()
            return m

    def advance_days(self, n):
        for _ in range(n):
            if self.db.day >= PERIOD_DAYS:
                break
            self.tick_day()

    def advance_to_period_end(self):
        self.advance_days(PERIOD_DAYS - self.db.day)

    def close_period(self):
        """Cierra el periodo (regla R4: SIMs en desborde vuelven a su origen) y abre el siguiente."""
        with self.db.lock, self.db.tx():
            closed = self.db.period
            self.db.exec("UPDATE moves SET state='rejected', last_error='Expirado por cierre de periodo', updated_day=? "
                         "WHERE state='proposed'", (self.db.abs_day,))
            audit(self.db, "orchestrator", "period.closing", "period", closed, {"day": self.db.day})
            self.db.period, self.db.day = closed + 1, 0
            self.platform.new_cycle()
            self.platform.reset_period()
            self.orch.auto_resolve_period_alerts()
            returned = self.orch.close_period_returns()
            self._save_clock()
            audit(self.db, "orchestrator", "period.closed", "period", closed, {"returned_from_overflow": returned})
        self.bump()
        return {"closed": closed, "returned": returned}

    def reset(self):
        with self.db.lock:
            self.running = False
            self.db.wipe()
            self.platform.reset_config()
            self.orch.last_api.clear()
            self.orch._batch_seq = 0
            self.apilog.clear()
            self.metrics.clear()
            self.scenario = None
            self._init_data()
        self.bump()
