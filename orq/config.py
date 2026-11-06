"""Constantes centralizadas del simulador."""

SEED = 20260101
PERIOD_DAYS = 30
BASE_TICK_S = 4.0            # segundos reales por día simulado a 1x
SPEEDS = (1, 5, 20)

POOL_CAPACITY_MB = 800.0
OVERFLOW_POOL = "POOL-OVF"
OPERATIONAL_POOLS = ("POOL-A", "POOL-B", "POOL-C")
ALL_POOLS = OPERATIONAL_POOLS + (OVERFLOW_POOL,)

PROFILE_RATE = {"bajo": 0.3, "medio": 1.0, "alto": 3.0}   # MB/día por SIM
LAYOUT = {
    "POOL-A": {"alto": 12, "medio": 6, "bajo": 2},
    "POOL-B": {"alto": 4, "medio": 8, "bajo": 8},
    "POOL-C": {"bajo": 20},
}
CITIES = ("Bogotá", "Medellín", "Cali", "Barranquilla", "Bucaramanga")
PLANS = ("M2M Básico", "M2M Plus", "M2M Pro")
NOISE = (0.8, 1.2)           # factor multiplicativo diario
ANOMALY_FACTOR = 4.0         # consumo diario > 4x la tasa del perfil => anomalía

DEFAULT_RULES = {
    "warn_threshold": 0.8,
    "critical_threshold": 1.0,
    "mode": "supervised",        # supervised | automatic
    "trigger": "projected",      # real | projected
    "min_history_days": 5,
    "max_moves_per_cycle": 6,
    "cooldown_days": 2,
}
MAX_ATTEMPTS = 3
DEFAULT_LATENCY_MS = 120
DEFAULT_RATE_LIMIT = {"enabled": True, "max_calls": 120, "retry_after_s": 5}
