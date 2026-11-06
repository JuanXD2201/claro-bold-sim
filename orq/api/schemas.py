"""Esquemas de entrada (validación) de la API."""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

PoolId = Field(min_length=1, max_length=20, pattern=r"^[A-Z0-9-]+$")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MoveBody(Strict):
    pool_id: str = PoolId


class BulkItem(Strict):
    iccid: str = Field(min_length=1, max_length=32)
    pool_id: str = PoolId
    idempotency_key: Optional[str] = Field(default=None, max_length=80)


class BulkMoveBody(Strict):
    moves: list[BulkItem] = Field(min_length=1, max_length=200)


class RulesPatch(Strict):
    warn_threshold: Optional[float] = Field(default=None, gt=0, le=1.5)
    critical_threshold: Optional[float] = Field(default=None, gt=0, le=2.0)
    mode: Optional[Literal["supervised", "automatic"]] = None
    trigger: Optional[Literal["real", "projected"]] = None
    min_history_days: Optional[int] = Field(default=None, ge=1, le=29)
    max_moves_per_cycle: Optional[int] = Field(default=None, ge=1, le=60)
    cooldown_days: Optional[int] = Field(default=None, ge=0, le=15)

    def patch(self):
        return self.model_dump(exclude_none=True)


class ManualMoveBody(Strict):
    iccid: str = Field(min_length=1, max_length=32)
    pool_id: str = PoolId
    reason: Optional[str] = Field(default=None, max_length=200)


class ManualBulkBody(Strict):
    items: list[ManualMoveBody] = Field(min_length=1, max_length=200)


class IdsBody(Strict):
    ids: list[int] = Field(min_length=1, max_length=200)


class FaultBody(Strict):
    scope: Literal["global", "sim"] = "global"
    iccid: Optional[str] = Field(default=None, max_length=32)
    kind: Literal["transient", "permanent"] = "transient"
    status: Optional[Literal[500, 503]] = None
    count: int = Field(default=3, ge=1, le=100)
    commit_then_fail: bool = False


class LatencyBody(Strict):
    latency_ms: int = Field(ge=0, le=5000)


class RateLimitBody(Strict):
    enabled: bool = True
    max_calls: int = Field(ge=1, le=10000)
    retry_after_s: int = Field(default=5, ge=1, le=300)


class SpikeBody(Strict):
    iccid: str = Field(min_length=1, max_length=32)
    multiplier: float = Field(default=15, ge=1, le=50)
    days: int = Field(default=2, ge=1, le=10)


class SpeedBody(Strict):
    speed: int
