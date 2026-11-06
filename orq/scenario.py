"""Escenarios precargados. Cada uno reinicia con la misma semilla, configura reglas/fallas y deja el reloj en pausa."""
from .platform_mock import PlatformError

SCENARIOS = {
    "normal": ("Normal", "Operación base sin fallas. Modo supervisado, proyección activa."),
    "pool_a_saturates": ("Pool A se satura",
                         "Avanza 4 días: A proyecta >100%. Hay propuestas pendientes de aprobar (modo supervisado)."),
    "api_failures": ("Fallas de API",
                     "Modo automático con 2 fallas 503 transitorias globales y una falla permanente en la SIM más "
                     "consumidora de A. Verás reintentos, un movimiento fallido y alertas."),
    "rate_limit": ("Límite de tasa",
                   "Modo automático con 3 llamadas por ciclo: la plataforma responde 429 y los movimientos se difieren."),
    "anomaly_spike": ("Pico anómalo en una SIM",
                      "Una SIM de bajo consumo de POOL-C multiplica x15 su consumo durante 2 días: alerta de anomalía."),
    "overflow_close": ("Desborde y cierre de periodo",
                       "Modo automático hasta el día 30: algunas SIMs terminan en POOL-OVF. Pulsa «Reiniciar periodo» "
                       "para ver el retorno (R4)."),
}


def list_scenarios():
    return [{"key": k, "name": v[0], "description": v[1]} for k, v in SCENARIOS.items()]


def apply_scenario(sim, key):
    if key not in SCENARIOS:
        raise PlatformError(404, "scenario_not_found", f"Escenario {key} no existe", details=list(SCENARIOS))
    sim.reset()
    o, p = sim.orch, sim.platform
    o.set_rules({"mode": "supervised", "trigger": "projected", "min_history_days": 3}, actor="scenario")
    if key == "pool_a_saturates":
        sim.advance_days(4)
    elif key == "api_failures":
        o.set_rules({"mode": "automatic"}, actor="scenario")
        sim.advance_days(2)
        top = sim.db.one("SELECT iccid FROM sims WHERE pool_id='POOL-A' ORDER BY period_mb DESC, iccid LIMIT 1")
        p.add_fault("global", kind="transient", status=503, count=2)
        p.add_fault("sim", iccid=top["iccid"], kind="permanent", status=500)
        sim.advance_days(3)
    elif key == "rate_limit":
        o.set_rules({"mode": "automatic"}, actor="scenario")
        p.set_rate_limit(True, 3, 5)
        sim.advance_days(4)
    elif key == "anomaly_spike":
        sim.advance_days(3)
        c = sim.db.one("SELECT iccid FROM sims WHERE pool_id='POOL-C' ORDER BY iccid LIMIT 1")
        p.inject_spike(c["iccid"], 15, 2)
        sim.advance_days(1)
    elif key == "overflow_close":
        o.set_rules({"mode": "automatic"}, actor="scenario")
        sim.advance_to_period_end()
    sim.scenario = key
    sim.bump()
    return {"scenario": key, "name": SCENARIOS[key][0], "clock": sim.clock()}
