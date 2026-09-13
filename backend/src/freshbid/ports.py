from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .models import (
    DecisionEvent,
    ForecastBundle,
    OutcomeRecord,
    PriceRecommendation,
    SafetyResult,
    StoreSnapshot,
)


class DemandForecaster(Protocol):
    """需求预测接口：简单模型、ARIMA、LSTM和Transformer均实现这个接口。"""

    def forecast(
        self,
        snapshot: StoreSnapshot,
        candidate_price_cents: tuple[int, ...],
        horizon_end: datetime,
    ) -> ForecastBundle: ...


class PricingPolicy(Protocol):
    """定价策略接口：第一版使用DP，未来Bandit也应返回同一种推荐结构。"""

    def recommend(
        self,
        snapshot: StoreSnapshot,
        forecast: ForecastBundle,
    ) -> PriceRecommendation: ...


class SafetyChecker(Protocol):
    """安全规则接口：检查价格档位、最低价和只降不升等硬约束。"""

    def check(
        self,
        snapshot: StoreSnapshot,
        recommendation: PriceRecommendation,
        checked_at: datetime,
    ) -> SafetyResult: ...


class PriceExecutor(Protocol):
    """价格执行接口：MVP由商家手动确认，未来可替换为POS适配器。"""

    def apply(self, event: DecisionEvent) -> DecisionEvent: ...


class OutcomeRepository(Protocol):
    """反馈存储接口：可先写本地文件，未来再替换成数据库。"""

    def append(self, outcome: OutcomeRecord) -> None: ...


class DecisionEventRepository(Protocol):
    """推荐状态事件接口：支持SQLite或未来的云数据库实现。"""

    def append_event(self, event: DecisionEvent) -> None: ...

    def list_events(self, recommendation_id: str) -> tuple[DecisionEvent, ...]: ...
