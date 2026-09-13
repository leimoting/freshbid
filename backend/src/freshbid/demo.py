from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from .forecasting import SimulatedDemandForecaster, SimulatedForecastConfig
from .lifecycle import RecommendationLifecycle
from .models import OutcomeRecord, StoreSnapshot
from .optimization import DynamicProgrammingConfig, RollingHorizonDP
from .safety import RuleBasedSafetyChecker, SafetyConfig
from .storage import SQLiteRepository


def main() -> None:
    # 创建一个固定的演示时间，确保每次运行都得到相同、便于检查的结果。
    observed_at = datetime(2026, 9, 14, 19, 0, tzinfo=timezone.utc)

    # 构造19:00时的门店快照。这里的数字只是演示输入，并非真实门店数据。
    snapshot = StoreSnapshot(
        # 每次运行使用新的ID，允许多次向同一个Demo数据库追加记录。
        snapshot_id=f"snapshot-demo-{uuid4().hex[:8]}",
        store_id="store-demo",
        product_id="croissant",
        product_name="Croissant",
        observed_at=observed_at,
        closes_at=observed_at + timedelta(hours=2),
        original_price_cents=2400,
        current_price_cents=2400,
        # 商家批准三个价格档位：HK$24.00、HK$21.60和HK$19.20。
        approved_price_cents=(2400, 2160, 1920),
        inventory_on_hand=36,
        recent_sales_units=5,
        recent_footfall=12,
        # 已进入晚间降价阶段，因此后续动作只能保持当前价或继续降价。
        markdown_started=True,
        last_price_change_at=None,
        price_changes_today=0,
        maximum_price_changes_per_day=2,
    )

    # 先打印通过验证的标准门店状态，便于以后与React或API的JSON格式对接。
    print(snapshot.model_dump_json(indent=2))
    # 再从商家批准的档位中筛选不高于当前价格的可行动作。
    feasible_prices = snapshot.feasible_price_cents()
    print("Feasible prices:", feasible_prices)

    # 使用中等价格敏感度生成从19:00到21:00的价格条件需求预测。
    # elasticity=1.0只是Demo假设，之后需要用真实数据校准并做敏感度测试。
    forecaster = SimulatedDemandForecaster(
        SimulatedForecastConfig(elasticity=1.0, trend_per_interval=-0.08)
    )
    forecast = forecaster.forecast(snapshot, feasible_prices, snapshot.closes_at)

    print("\nDemand forecast until closing")
    print("Assumptions:")
    for note in forecast.assumption_notes:
        print(f"- {note}")

    for scenario in forecast.scenarios:
        print(f"\nPrice HK${scenario.price_cents / 100:.2f}")
        for interval in scenario.intervals:
            print(
                f"{interval.start_at:%H:%M}-{interval.end_at:%H:%M}  "
                f"expected={interval.expected_units:.2f}  "
                f"P10={interval.p10_units:.2f}  "
                f"P50={interval.p50_units:.2f}  "
                f"P90={interval.p90_units:.2f}"
            )

    # DP使用完整的概率需求场景，比较所有合法的小时价格路径。
    optimizer = RollingHorizonDP(
        DynamicProgrammingConfig(
            leftover_penalty_cents_per_unit=300,
            price_change_penalty_cents=50,
        )
    )
    recommendation = optimizer.recommend(snapshot, forecast)

    print("\nDynamic pricing recommendation")
    print(f"Recommended price: HK${recommendation.recommended_price_cents / 100:.2f}")
    print(f"Expected next-hour sales: {recommendation.next_hour_expected_sales:.2f}")
    print(
        "Expected next-hour revenue: "
        f"HK${recommendation.next_hour_expected_revenue_cents / 100:.2f}"
    )
    print(
        "Probability of selling all inventory by closing: "
        f"{recommendation.sellout_probability_by_close:.1%}"
    )
    print(f"Expected inventory at closing: {recommendation.expected_inventory_at_close:.2f}")
    print(
        "Representative hourly price path: ",
        " -> ".join(f"HK${price / 100:.2f}" for price in recommendation.representative_price_path_cents),
        sep="",
    )
    print("Initial action values:")
    for item in recommendation.candidate_action_values:
        print(
            f"- HK${item.price_cents / 100:.2f}: "
            f"objective HK${item.expected_total_objective_cents / 100:.2f}"
        )
    print("Reason codes:", ", ".join(recommendation.reason_codes))

    # 推荐必须先通过硬规则，之后才能由商家批准。
    safety_checker = RuleBasedSafetyChecker(
        SafetyConfig(
            maximum_recommendation_age_minutes=15,
            minimum_minutes_between_price_changes=60,
            minimum_price_cents=1920,
        )
    )
    safety_result = safety_checker.check(snapshot, recommendation, observed_at)
    print("\nSafety check:", "PASSED" if safety_result.passed else "FAILED")
    if safety_result.violation_codes:
        print("Violations:", ", ".join(safety_result.violation_codes))

    # 用SQLite保存完整状态。这里自动模拟商家批准和价格生效，React版本将由按钮触发。
    database_path = Path(__file__).resolve().parents[2] / "data" / "freshbid_demo.sqlite3"
    with SQLiteRepository(database_path) as repository:
        lifecycle = RecommendationLifecycle(repository, repository)
        lifecycle.record_recommended(recommendation)
        lifecycle.approve(
            recommendation,
            safety_result,
            occurred_at=observed_at + timedelta(minutes=1),
            actor="demo-manager",
        )
        lifecycle.apply(
            recommendation,
            occurred_at=observed_at + timedelta(minutes=2),
            actor="demo-manager",
            effective_price_cents=recommendation.recommended_price_cents,
        )
        lifecycle.verify(
            recommendation,
            occurred_at=observed_at + timedelta(minutes=3),
            actor="demo-manager",
            effective_price_cents=recommendation.recommended_price_cents,
        )

        # 这一小时的结果是模拟值，并明确标记source=simulated。
        outcome = OutcomeRecord(
            recommendation_id=recommendation.recommendation_id,
            interval_start=observed_at,
            interval_end=observed_at + timedelta(hours=1),
            effective_price_cents=recommendation.recommended_price_cents,
            units_sold=11,
            ending_inventory=25,
            policy_version=recommendation.policy_version,
            model_version=forecast.model_version,
            source="simulated",
        )
        lifecycle.record_outcome(outcome)

        print("\nRecommendation lifecycle")
        for event in repository.list_events(recommendation.recommendation_id):
            price_text = (
                f" at HK${event.effective_price_cents / 100:.2f}"
                if event.effective_price_cents is not None
                else ""
            )
            print(f"- {event.event_type.value}{price_text} by {event.actor}")
        print(
            f"Recorded simulated outcome: sold {outcome.units_sold}, "
            f"ending inventory {outcome.ending_inventory}"
        )
        print(f"SQLite database: {database_path}")


if __name__ == "__main__":
    main()
