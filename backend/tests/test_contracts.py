import unittest
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError

from freshbid.forecasting import SimulatedDemandForecaster, SimulatedForecastConfig
from freshbid.models import (
    DecisionEvent,
    DecisionEventType,
    DemandInterval,
    StoreSnapshot,
)
from freshbid.optimization import DynamicProgrammingConfig, RollingHorizonDP


class ContractTests(unittest.TestCase):
    def make_snapshot(self) -> StoreSnapshot:
        # 测试场景已经从原价2400降到2160，因此不能再次选择2400。
        now = datetime(2026, 9, 14, 19, 0, tzinfo=timezone.utc)
        return StoreSnapshot(
            snapshot_id="s1",
            store_id="store-1",
            product_id="p1",
            product_name="Croissant",
            observed_at=now,
            closes_at=now + timedelta(hours=2),
            original_price_cents=2400,
            current_price_cents=2160,
            approved_price_cents=(2400, 2160, 1920),
            inventory_on_hand=36,
            recent_sales_units=5,
            markdown_started=True,
        )

    def test_markdown_actions_never_increase_price(self) -> None:
        # 只允许保持2160或继续下降到1920。
        self.assertEqual(self.make_snapshot().feasible_price_cents(), (2160, 1920))

    def test_forecast_quantiles_must_be_ordered(self) -> None:
        # p10高于p50属于无效概率预测，应在进入DP前被拒绝。
        now = datetime(2026, 9, 14, 19, 0, tzinfo=timezone.utc)
        with self.assertRaises(ValidationError):
            DemandInterval(
                start_at=now,
                end_at=now + timedelta(minutes=30),
                expected_units=5,
                p10_units=7,
                p50_units=5,
                p90_units=9,
            )

    def test_verified_event_requires_effective_price(self) -> None:
        # 没有实际生效价格的verified事件无法用于后续销量归因。
        now = datetime(2026, 9, 14, 19, 0, tzinfo=timezone.utc)
        with self.assertRaises(ValidationError):
            DecisionEvent(
                recommendation_id="r1",
                event_type=DecisionEventType.VERIFIED,
                occurred_at=now,
                actor="manager",
            )

    def test_forecaster_returns_one_scenario_per_feasible_price(self) -> None:
        snapshot = self.make_snapshot()
        forecast = SimulatedDemandForecaster().forecast(
            snapshot,
            snapshot.feasible_price_cents(),
            snapshot.closes_at,
        )
        self.assertEqual([item.price_cents for item in forecast.scenarios], [2160, 1920])
        self.assertTrue(all(len(item.intervals) == 4 for item in forecast.scenarios))

    def test_lower_price_increases_expected_demand_under_positive_elasticity(self) -> None:
        snapshot = self.make_snapshot()
        forecast = SimulatedDemandForecaster(
            SimulatedForecastConfig(elasticity=1.0)
        ).forecast(snapshot, (2160, 1920), snapshot.closes_at)
        demand_at_2160 = forecast.scenarios[0].intervals[0].expected_units
        demand_at_1920 = forecast.scenarios[1].intervals[0].expected_units
        self.assertGreater(demand_at_1920, demand_at_2160)

    def test_forecaster_rejects_price_outside_merchant_tiers(self) -> None:
        snapshot = self.make_snapshot()
        with self.assertRaises(ValueError):
            SimulatedDemandForecaster().forecast(snapshot, (2050,), snapshot.closes_at)

    def test_dp_recommendation_uses_a_feasible_price(self) -> None:
        snapshot = self.make_snapshot()
        forecast = SimulatedDemandForecaster().forecast(
            snapshot, snapshot.feasible_price_cents(), snapshot.closes_at
        )
        recommendation = RollingHorizonDP().recommend(snapshot, forecast)
        self.assertIn(recommendation.recommended_price_cents, snapshot.feasible_price_cents())
        self.assertTrue(
            all(
                later <= earlier
                for earlier, later in zip(
                    recommendation.representative_price_path_cents,
                    recommendation.representative_price_path_cents[1:],
                )
            )
        )

    def test_dp_returns_probability_and_inventory_metrics(self) -> None:
        snapshot = self.make_snapshot()
        forecast = SimulatedDemandForecaster().forecast(
            snapshot, snapshot.feasible_price_cents(), snapshot.closes_at
        )
        recommendation = RollingHorizonDP(
            DynamicProgrammingConfig(leftover_penalty_cents_per_unit=300)
        ).recommend(snapshot, forecast)
        self.assertGreaterEqual(recommendation.sellout_probability_by_close, 0)
        self.assertLessEqual(recommendation.sellout_probability_by_close, 1)
        self.assertGreaterEqual(recommendation.expected_inventory_at_close, 0)
        self.assertLessEqual(
            recommendation.expected_inventory_at_close, snapshot.inventory_on_hand
        )

    def test_dp_rejects_forecast_for_another_snapshot(self) -> None:
        snapshot = self.make_snapshot()
        forecast = SimulatedDemandForecaster().forecast(
            snapshot, snapshot.feasible_price_cents(), snapshot.closes_at
        )
        other_snapshot = StoreSnapshot(
            **{**snapshot.model_dump(), "snapshot_id": "different-snapshot"}
        )
        with self.assertRaises(ValueError):
            RollingHorizonDP().recommend(other_snapshot, forecast)


if __name__ == "__main__":
    unittest.main()
