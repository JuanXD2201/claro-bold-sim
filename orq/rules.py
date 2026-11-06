"""Reglas del orquestador: validación y planificador puro (sin I/O) de movimientos."""
from .config import DEFAULT_RULES, OPERATIONAL_POOLS, OVERFLOW_POOL, PERIOD_DAYS
from .platform_mock import PlatformError

MODES = ("supervised", "automatic")
TRIGGERS = ("real", "projected")


def validate_rules(rules):
    """Valida y normaliza un dict de reglas completo. Lanza PlatformError(422)."""
    r = {**DEFAULT_RULES, **rules}
    errs = []
    try:
        r["warn_threshold"] = float(r["warn_threshold"])
        r["critical_threshold"] = float(r["critical_threshold"])
        r["min_history_days"] = int(r["min_history_days"])
        r["max_moves_per_cycle"] = int(r["max_moves_per_cycle"])
        r["cooldown_days"] = int(r["cooldown_days"])
    except (TypeError, ValueError):
        raise PlatformError(422, "invalid_rules", "Valores numéricos inválidos en reglas")
    if not 0 < r["warn_threshold"] <= 1.5:
        errs.append("warn_threshold debe estar en (0, 1.5]")
    if not r["warn_threshold"] <= r["critical_threshold"] <= 2.0:
        errs.append("critical_threshold debe ser >= warn_threshold y <= 2.0")
    if r["mode"] not in MODES:
        errs.append(f"mode debe ser uno de {MODES}")
    if r["trigger"] not in TRIGGERS:
        errs.append(f"trigger debe ser uno de {TRIGGERS}")
    if not 1 <= r["min_history_days"] <= PERIOD_DAYS - 1:
        errs.append("min_history_days debe estar entre 1 y 29")
    if not 1 <= r["max_moves_per_cycle"] <= 60:
        errs.append("max_moves_per_cycle debe estar entre 1 y 60")
    if not 0 <= r["cooldown_days"] <= 15:
        errs.append("cooldown_days debe estar entre 0 y 15")
    if errs:
        raise PlatformError(422, "invalid_rules", "; ".join(errs), details=errs)
    return {k: r[k] for k in DEFAULT_RULES}


def project(pool, days_left):
    return pool["used"] + days_left * sum(m["avg"] for m in pool["members"] if m["active"])


def metric(pool, rules, days_left):
    if pool["cap"] <= 0:
        return 0.0
    v = project(pool, days_left) if rules["trigger"] == "projected" else pool["used"]
    return v / pool["cap"]


def plan_moves(state, rules, day):
    """state: {pid: {"cap","used","members":[{"iccid","avg","active","blocked"}]}} (se muta una copia).
    Devuelve lista de propuestas {iccid, from_pool, to_pool, reason}."""
    if day <= 0 or (rules["trigger"] == "projected" and day < rules["min_history_days"]):
        return []
    days_left = PERIOD_DAYS - day
    pools = {k: {**v, "members": [dict(m) for m in v["members"]]} for k, v in state.items()}
    out = []
    for src in sorted(OPERATIONAL_POOLS, key=lambda p: -metric(pools[p], rules, days_left)):
        while len(out) < rules["max_moves_per_cycle"]:
            sp = pools[src]
            if metric(sp, rules, days_left) < rules["warn_threshold"]:
                break
            need = project(sp, days_left) - sp["cap"] * rules["critical_threshold"]
            if need <= 0:
                break
            cands = [m for m in sp["members"] if m["active"] and not m["blocked"] and m["avg"] * days_left > 0]
            if not cands:
                break
            enough = [m for m in cands if m["avg"] * days_left >= need]
            sim = min(enough, key=lambda m: m["avg"]) if enough else max(cands, key=lambda m: m["avg"])
            gain = sim["avg"] * days_left
            best, best_after = None, None
            for t in OPERATIONAL_POOLS:
                if t == src:
                    continue
                after = (project(pools[t], days_left) + gain) / pools[t]["cap"]
                if after <= rules["warn_threshold"] and (best is None or after < best_after):
                    best, best_after = t, after
            dest = best or OVERFLOW_POOL
            sp["members"].remove(next(m for m in sp["members"] if m["iccid"] == sim["iccid"]))
            if dest in pools:
                pools[dest]["members"].append({**sim, "blocked": True})
            ratio = project(sp, days_left) / sp["cap"] + gain / sp["cap"]
            out.append({"iccid": sim["iccid"], "from_pool": src, "to_pool": dest,
                        "reason": f"{src} {('proyectado' if rules['trigger'] == 'projected' else 'real')} "
                                  f"{ratio:.0%} de capacidad; SIM de {sim['avg']:.2f} MB/día a {dest}"})
    return out
