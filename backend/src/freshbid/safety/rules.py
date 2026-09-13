from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from freshbid.models import PriceRecommendation, SafetyResult, StoreSnapshot


@dataclass(frozen=True)
class SafetyConfig:
    """商家可配置的确定性安全规则。"""

    maximum_recommendation_age_minutes: int = 15
    minimum_minutes_between_price_changes: int = 60
    minimum_price_cents: int | None = None

    def __post_init__(self) -> None:
        if self.maximum_recommendation_age_minutes <= 0:
            raise ValueError("maximum recommendation age must be positive")
        if self.minimum_minutes_between_price_changes < 0:
            raise ValueError("minimum change interval cannot be negative")
        if self.minimum_price_cents is not None and self.minimum_price_cents <= 0:
            raise ValueError("minimum price must be positive")


class RuleBasedSafetyChecker:
    """在商家看到推荐前重新检查所有硬约束。"""

    def __init__(self, config: SafetyConfig | None = None) -> None:
        self.config = config or SafetyConfig()

    def check(
        self,
        snapshot: StoreSnapshot,
        recommendation: PriceRecommendation,
        checked_at: datetime,
    ) -> SafetyResult:
        violations: list[str] = []
        price = recommendation.recommended_price_cents
        price_is_changing = price != snapshot.current_price_cents

        if recommendation.snapshot_id != snapshot.snapshot_id:
            violations.append("SNAPSHOT_MISMATCH")
        if checked_at >= snapshot.closes_at:
            violations.append("STORE_CLOSED")
        if checked_at - recommendation.generated_at > timedelta(
            minutes=self.config.maximum_recommendation_age_minutes
        ):
            violations.append("RECOMMENDATION_EXPIRED")
        if price not in snapshot.approved_price_cents:
            violations.append("PRICE_NOT_APPROVED")
        if snapshot.markdown_started and price > snapshot.current_price_cents:
            violations.append("PRICE_INCREASE_NOT_ALLOWED")
        if self.config.minimum_price_cents is not None:
            if price < self.config.minimum_price_cents:
                violations.append("PRICE_BELOW_FLOOR")

        # 保持当前价格不算一次调价，因此不消耗每日次数，也不受间隔限制。
        if price_is_changing:
            if snapshot.price_changes_today >= snapshot.maximum_price_changes_per_day:
                violations.append("MAX_DAILY_PRICE_CHANGES_REACHED")
            if snapshot.last_price_change_at is not None:
                earliest_next_change = snapshot.last_price_change_at + timedelta(
                    minutes=self.config.minimum_minutes_between_price_changes
                )
                if checked_at < earliest_next_change:
                    violations.append("PRICE_CHANGE_TOO_SOON")

        return SafetyResult(
            recommendation_id=recommendation.recommendation_id,
            checked_at=checked_at,
            passed=not violations,
            violation_codes=tuple(violations),
        )
