"""Carga reproducible de datos sintéticos (60 SIMs, 3 pools + desborde)."""
import random

from .config import (ALL_POOLS, CITIES, LAYOUT, OVERFLOW_POOL, PLANS, POOL_CAPACITY_MB,
                     PROFILE_RATE, SEED)

KINDS = ("Panadería", "Tienda", "Droguería", "Restaurante", "Papelería", "Ferretería", "Cafetería", "Frutería")
NAMES = ("El Roble", "La Esquina", "Los Andes", "Santa Rita", "El Mirador", "Don Pepe", "La Palma", "El Faro",
         "Buenavista", "Las Flores")


def seed_database(db, seed=SEED):
    """Puebla pools y sims. Mismo seed => mismos datos."""
    rng = random.Random(seed)
    with db.tx():
        for pid in ALL_POOLS:
            ovf = pid == OVERFLOW_POOL
            db.exec("INSERT INTO pools(id,capacity_mb,is_overflow) VALUES(?,?,?)",
                    (pid, 0.0 if ovf else POOL_CAPACITY_MB, int(ovf)))
        n = 0
        for pid, mix in LAYOUT.items():
            profiles = [p for p, c in mix.items() for _ in range(c)]
            rng.shuffle(profiles)
            for prof in profiles:
                n += 1
                iccid = "89571" + f"{rng.randrange(10**14):014d}"          # 19 dígitos, ficticio
                msisdn = "+57300" + f"{rng.randrange(10**7):07d}"
                terminal = f"BD-{rng.randrange(10**6):06d}"
                merchant = f"{rng.choice(KINDS)} {rng.choice(NAMES)} {n:02d}"
                db.exec("INSERT INTO sims(iccid,msisdn,terminal_id,merchant,city,plan,status,profile,pool_id,"
                        "origin_pool,base_rate) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (iccid, msisdn, terminal, merchant, rng.choice(CITIES), rng.choice(PLANS), "activa",
                         prof, pid, pid, PROFILE_RATE[prof]))
