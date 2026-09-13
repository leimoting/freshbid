import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from freshbid.forecasting import SimulatedDemandForecaster
from freshbid.lifecycle import RecommendationLifecycle
from freshbid.models import DecisionEventType, OutcomeRecord, StoreSnapshot
from freshbid.optimization import RollingHorizonDP
from freshbid.safety import RuleBasedSafetyChecker, SafetyConfig
from freshbid.storage import SQLiteRepository


class SafetyAndLifecycleTests(unittest.TestCase):
    def make_recommendation(self):
        now = datetime(2026, 9, 14, 19, 0, tzinfo=timezone.utc)
        snapshot = StoreSnapshot(
            snapshot_id="safety-snapshot",
            store_id="store-1",
            product_id="p1",
            product_name="Croissant",
            observed_at=now,
            closes_at=now + timedelta(hours=2),
            original_price_cents=2400,
            current_price_cents=2400,
            approved_price_cents=(2400, 2160, 1920),
            inventory_on_hand=36,
            recent_sales_units=5,
            markdown_started=True,
            maximum_price_changes_per_day=2,
        )
        forecast = SimulatedDemandForecaster().forecast(
            snapshot, snapshot.feasible_price_cents(), snapshot.closes_at
        )
        recommendation = RollingHorizonDP().recommend(snapshot, forecast)
        return now, snapshot, forecast, recommendation

    def test_valid_recommendation_passes_safety_rules(self) -> None:
        now, snapshot, _forecast, recommendation = self.make_recommendation()
        result = RuleBasedSafetyChecker(
            SafetyConfig(minimum_price_cents=1920)
        ).check(snapshot, recommendation, now)
        self.assertTrue(result.passed)

    def test_stale_recommendation_fails_safety_rules(self) -> None:
        now, snapshot, _forecast, recommendation = self.make_recommendation()
        result = RuleBasedSafetyChecker().check(
            snapshot, recommendation, now + timedelta(minutes=16)
        )
        self.assertFalse(result.passed)
        self.assertIn("RECOMMENDATION_EXPIRED", result.violation_codes)

    def test_lifecycle_and_simulated_outcome_are_persisted(self) -> None:
        now, snapshot, forecast, recommendation = self.make_recommendation()
        safety = RuleBasedSafetyChecker().check(snapshot, recommendation, now)
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "test.sqlite3"
            with SQLiteRepository(database) as repository:
                lifecycle = RecommendationLifecycle(repository, repository)
                lifecycle.record_recommended(recommendation)
                lifecycle.approve(recommendation, safety, now, "manager")
                lifecycle.apply(
                    recommendation,
                    now + timedelta(minutes=1),
                    "manager",
                    recommendation.recommended_price_cents,
                )
                lifecycle.verify(
                    recommendation,
                    now + timedelta(minutes=2),
                    "manager",
                    recommendation.recommended_price_cents,
                )
                lifecycle.record_outcome(
                    OutcomeRecord(
                        recommendation_id=recommendation.recommendation_id,
                        interval_start=now,
                        interval_end=now + timedelta(hours=1),
                        effective_price_cents=recommendation.recommended_price_cents,
                        units_sold=11,
                        ending_inventory=25,
                        policy_version=recommendation.policy_version,
                        model_version=forecast.model_version,
                        source="simulated",
                    )
                )

                events = repository.list_events(recommendation.recommendation_id)
                self.assertEqual(
                    [event.event_type for event in events],
                    [
                        DecisionEventType.RECOMMENDED,
                        DecisionEventType.APPROVED,
                        DecisionEventType.APPLIED,
                        DecisionEventType.VERIFIED,
                    ],
                )
                outcomes = repository.list_outcomes(recommendation.recommendation_id)
                self.assertEqual(len(outcomes), 1)
                self.assertEqual(outcomes[0].source, "simulated")

    def test_price_cannot_be_applied_before_approval(self) -> None:
        now, _snapshot, _forecast, recommendation = self.make_recommendation()
        with SQLiteRepository(":memory:") as repository:
            lifecycle = RecommendationLifecycle(repository, repository)
            lifecycle.record_recommended(recommendation)
            with self.assertRaises(ValueError):
                lifecycle.apply(
                    recommendation,
                    now,
                    "manager",
                    recommendation.recommended_price_cents,
                )


if __name__ == "__main__":
    unittest.main()
