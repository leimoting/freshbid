from __future__ import annotations

from datetime import datetime

from freshbid.models import (
    DecisionEvent,
    DecisionEventType,
    OutcomeRecord,
    PriceRecommendation,
    SafetyResult,
)
from freshbid.ports import DecisionEventRepository, OutcomeRepository


class RecommendationLifecycle:
    """控制推荐从生成到验证的合法状态转换。"""

    def __init__(
        self,
        event_repository: DecisionEventRepository,
        outcome_repository: OutcomeRepository,
    ) -> None:
        self.event_repository = event_repository
        self.outcome_repository = outcome_repository

    def record_recommended(self, recommendation: PriceRecommendation) -> DecisionEvent:
        if self.event_repository.list_events(recommendation.recommendation_id):
            raise ValueError("recommendation has already been registered")
        event = DecisionEvent(
            recommendation_id=recommendation.recommendation_id,
            event_type=DecisionEventType.RECOMMENDED,
            occurred_at=recommendation.generated_at,
            actor="pricing-system",
        )
        self.event_repository.append_event(event)
        return event

    def approve(
        self,
        recommendation: PriceRecommendation,
        safety_result: SafetyResult,
        occurred_at: datetime,
        actor: str,
    ) -> DecisionEvent:
        self._require_last_state(recommendation.recommendation_id, DecisionEventType.RECOMMENDED)
        if safety_result.recommendation_id != recommendation.recommendation_id:
            raise ValueError("safety result does not belong to this recommendation")
        if not safety_result.passed:
            raise ValueError("unsafe recommendation cannot be approved")
        event = DecisionEvent(
            recommendation_id=recommendation.recommendation_id,
            event_type=DecisionEventType.APPROVED,
            occurred_at=occurred_at,
            actor=actor,
        )
        self.event_repository.append_event(event)
        return event

    def reject(
        self,
        recommendation: PriceRecommendation,
        occurred_at: datetime,
        actor: str,
    ) -> DecisionEvent:
        self._require_last_state(recommendation.recommendation_id, DecisionEventType.RECOMMENDED)
        event = DecisionEvent(
            recommendation_id=recommendation.recommendation_id,
            event_type=DecisionEventType.REJECTED,
            occurred_at=occurred_at,
            actor=actor,
        )
        self.event_repository.append_event(event)
        return event

    def apply(
        self,
        recommendation: PriceRecommendation,
        occurred_at: datetime,
        actor: str,
        effective_price_cents: int,
    ) -> DecisionEvent:
        self._require_last_state(recommendation.recommendation_id, DecisionEventType.APPROVED)
        if effective_price_cents != recommendation.recommended_price_cents:
            raise ValueError("applied price must match the approved recommendation")
        event = DecisionEvent(
            recommendation_id=recommendation.recommendation_id,
            event_type=DecisionEventType.APPLIED,
            occurred_at=occurred_at,
            actor=actor,
            effective_price_cents=effective_price_cents,
        )
        self.event_repository.append_event(event)
        return event

    def verify(
        self,
        recommendation: PriceRecommendation,
        occurred_at: datetime,
        actor: str,
        effective_price_cents: int,
    ) -> DecisionEvent:
        self._require_last_state(recommendation.recommendation_id, DecisionEventType.APPLIED)
        if effective_price_cents != recommendation.recommended_price_cents:
            raise ValueError("verified price must match the approved recommendation")
        event = DecisionEvent(
            recommendation_id=recommendation.recommendation_id,
            event_type=DecisionEventType.VERIFIED,
            occurred_at=occurred_at,
            actor=actor,
            effective_price_cents=effective_price_cents,
        )
        self.event_repository.append_event(event)
        return event

    def record_outcome(self, outcome: OutcomeRecord) -> None:
        self._require_last_state(outcome.recommendation_id, DecisionEventType.VERIFIED)
        self.outcome_repository.append(outcome)

    def _require_last_state(
        self,
        recommendation_id: str,
        required_state: DecisionEventType,
    ) -> None:
        events = self.event_repository.list_events(recommendation_id)
        if not events or events[-1].event_type != required_state:
            actual = events[-1].event_type.value if events else "none"
            raise ValueError(
                f"expected lifecycle state {required_state.value}, current state is {actual}"
            )
