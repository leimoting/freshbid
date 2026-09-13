from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from freshbid.forecasting import SimulatedDemandForecaster, SimulatedForecastConfig
from freshbid.lifecycle import RecommendationLifecycle
from freshbid.models import ForecastBundle, OutcomeRecord, PriceRecommendation, SafetyResult, StoreSnapshot
from freshbid.optimization import DynamicProgrammingConfig, RollingHorizonDP
from freshbid.safety import RuleBasedSafetyChecker, SafetyConfig
from freshbid.storage import SQLiteRepository


class DemoRecommendationRequest(BaseModel):
    """浏览器Demo提交的最小门店数据。"""

    model_config = ConfigDict(extra="forbid")

    product_name: str = Field(default="Croissant", min_length=1, max_length=80)
    inventory_on_hand: int = Field(default=36, ge=0, le=500)
    recent_sales_units: int = Field(default=5, ge=0, le=500)
    recent_footfall: int | None = Field(default=12, ge=0, le=5000)
    original_price_cents: int = Field(default=2400, gt=0)
    current_price_cents: int = Field(default=2400, gt=0)
    approved_price_cents: tuple[int, ...] = (2400, 2160, 1920)
    hours_until_close: int = Field(default=2, ge=1, le=8)


class OutcomeRequest(BaseModel):
    """价格执行一小时后由商家填写的真实结果。"""

    model_config = ConfigDict(extra="forbid")

    units_sold: int = Field(ge=0, le=500)
    ending_inventory: int = Field(ge=0, le=500)
    source: str = Field(default="observed", pattern="^(observed|simulated)$")


@dataclass
class RecommendationSession:
    snapshot: StoreSnapshot
    forecast: ForecastBundle
    recommendation: PriceRecommendation
    safety: SafetyResult


class FreshBidService:
    """连接预测、DP、安全检查和留档模块的应用服务。

    API只调用这一层，因此未来将模拟预测替换成Transformer时，无需重写网页或路由。
    """

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.sessions: dict[str, RecommendationSession] = {}
        self.forecaster = SimulatedDemandForecaster(
            SimulatedForecastConfig(elasticity=1.0, trend_per_interval=-0.08)
        )
        self.optimizer = RollingHorizonDP(
            DynamicProgrammingConfig(
                leftover_penalty_cents_per_unit=300,
                price_change_penalty_cents=50,
            )
        )

    def create_recommendation(self, request: DemoRecommendationRequest) -> dict:
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        tiers = tuple(sorted(set(request.approved_price_cents), reverse=True))
        if request.current_price_cents not in tiers:
            tiers = tuple(sorted((*tiers, request.current_price_cents), reverse=True))

        snapshot = StoreSnapshot(
            snapshot_id=f"snapshot-{uuid4().hex[:10]}",
            store_id="store-demo",
            product_id=request.product_name.lower().replace(" ", "-"),
            product_name=request.product_name,
            observed_at=now,
            closes_at=now + timedelta(hours=request.hours_until_close),
            original_price_cents=request.original_price_cents,
            current_price_cents=request.current_price_cents,
            approved_price_cents=tiers,
            inventory_on_hand=request.inventory_on_hand,
            recent_sales_units=request.recent_sales_units,
            recent_footfall=request.recent_footfall,
            markdown_started=True,
            maximum_price_changes_per_day=2,
        )
        forecast = self.forecaster.forecast(
            snapshot, snapshot.feasible_price_cents(), snapshot.closes_at
        )
        recommendation = self.optimizer.recommend(snapshot, forecast)
        # 当前Demo把商家批准的最低档位同时视为价格底线。
        safety_checker = RuleBasedSafetyChecker(
            SafetyConfig(
                maximum_recommendation_age_minutes=15,
                minimum_minutes_between_price_changes=60,
                minimum_price_cents=min(tiers),
            )
        )
        safety = safety_checker.check(snapshot, recommendation, now)
        session = RecommendationSession(snapshot, forecast, recommendation, safety)
        self.sessions[recommendation.recommendation_id] = session

        with SQLiteRepository(self.database_path) as repository:
            RecommendationLifecycle(repository, repository).record_recommended(recommendation)
        return self.get_recommendation(recommendation.recommendation_id)

    def get_recommendation(self, recommendation_id: str) -> dict:
        session = self._session(recommendation_id)
        with SQLiteRepository(self.database_path) as repository:
            events = repository.list_events(recommendation_id)
            outcomes = repository.list_outcomes(recommendation_id)
        status = events[-1].event_type.value if events else "unknown"
        return {
            "status": status,
            "snapshot": session.snapshot.model_dump(mode="json"),
            "forecast": session.forecast.model_dump(mode="json"),
            "recommendation": session.recommendation.model_dump(mode="json"),
            "safety": session.safety.model_dump(mode="json"),
            "events": [event.model_dump(mode="json") for event in events],
            "outcomes": [outcome.model_dump(mode="json") for outcome in outcomes],
        }

    def approve(self, recommendation_id: str, actor: str) -> dict:
        session = self._session(recommendation_id)
        with SQLiteRepository(self.database_path) as repository:
            RecommendationLifecycle(repository, repository).approve(
                session.recommendation, session.safety, datetime.now(timezone.utc), actor
            )
        return self.get_recommendation(recommendation_id)

    def reject(self, recommendation_id: str, actor: str) -> dict:
        session = self._session(recommendation_id)
        with SQLiteRepository(self.database_path) as repository:
            RecommendationLifecycle(repository, repository).reject(
                session.recommendation, datetime.now(timezone.utc), actor
            )
        return self.get_recommendation(recommendation_id)

    def apply(self, recommendation_id: str, actor: str) -> dict:
        session = self._session(recommendation_id)
        with SQLiteRepository(self.database_path) as repository:
            RecommendationLifecycle(repository, repository).apply(
                session.recommendation,
                datetime.now(timezone.utc),
                actor,
                session.recommendation.recommended_price_cents,
            )
        return self.get_recommendation(recommendation_id)

    def verify(self, recommendation_id: str, actor: str) -> dict:
        session = self._session(recommendation_id)
        with SQLiteRepository(self.database_path) as repository:
            RecommendationLifecycle(repository, repository).verify(
                session.recommendation,
                datetime.now(timezone.utc),
                actor,
                session.recommendation.recommended_price_cents,
            )
        return self.get_recommendation(recommendation_id)

    def record_outcome(self, recommendation_id: str, request: OutcomeRequest) -> dict:
        session = self._session(recommendation_id)
        interval_end = min(
            datetime.now(timezone.utc),
            session.snapshot.observed_at + timedelta(hours=1),
        )
        # 快速Demo可能在生成推荐后立即提交结果，仍需让反馈区间保持有效。
        if interval_end <= session.snapshot.observed_at:
            interval_end = session.snapshot.observed_at + timedelta(minutes=1)
        outcome = OutcomeRecord(
            recommendation_id=recommendation_id,
            interval_start=session.snapshot.observed_at,
            interval_end=interval_end,
            effective_price_cents=session.recommendation.recommended_price_cents,
            units_sold=request.units_sold,
            ending_inventory=request.ending_inventory,
            policy_version=session.recommendation.policy_version,
            model_version=session.forecast.model_version,
            source=request.source,
        )
        with SQLiteRepository(self.database_path) as repository:
            RecommendationLifecycle(repository, repository).record_outcome(outcome)
        return self.get_recommendation(recommendation_id)

    def _session(self, recommendation_id: str) -> RecommendationSession:
        try:
            return self.sessions[recommendation_id]
        except KeyError as error:
            raise KeyError("recommendation not found; create a new demo recommendation") from error
