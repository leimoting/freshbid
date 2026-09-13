from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import sqrt

from freshbid.models import (
    DemandInterval,
    ForecastBundle,
    PriceScenarioForecast,
    StoreSnapshot,
)


@dataclass(frozen=True)
class SimulatedForecastConfig:
    """模拟预测参数。

    这些参数只用于打通MVP和测试DP，不代表真实门店的价格反应。
    后续应通过门店数据校准，或者由经过验证的预测模型替换。
    """

    # 价格弹性大于0。价格下降时，预测需求会按幂函数增加。
    elasticity: float = 1.0
    # 每经过一个30分钟区间，基础需求的相对变化。例如-0.08表示下降8%。
    trend_per_interval: float = -0.08
    # 防止临近打烊时预测均值下降为负数或完全归零。
    minimum_time_multiplier: float = 0.35
    interval_minutes: int = 30
    model_name: str = "simulated-price-response"
    model_version: str = "0.1.0"

    def __post_init__(self) -> None:
        if self.elasticity < 0:
            raise ValueError("elasticity must be non-negative")
        if self.interval_minutes != 30:
            raise ValueError("the MVP contract currently requires 30-minute intervals")
        if self.minimum_time_multiplier <= 0:
            raise ValueError("minimum_time_multiplier must be positive")


class SimulatedDemandForecaster:
    """使用近期销量和价格弹性生成可重复的需求场景。

    该实现用于验证模块接口和支持早期DP Demo。它不是训练完成的需求模型。
    """

    def __init__(self, config: SimulatedForecastConfig | None = None) -> None:
        self.config = config or SimulatedForecastConfig()

    def forecast(
        self,
        snapshot: StoreSnapshot,
        candidate_price_cents: tuple[int, ...],
        horizon_end: datetime,
    ) -> ForecastBundle:
        """为每个候选价格生成从当前时刻到horizon_end的30分钟预测。"""

        self._validate_request(snapshot, candidate_price_cents, horizon_end)

        scenarios = tuple(
            PriceScenarioForecast(
                price_cents=price_cents,
                intervals=self._forecast_price_scenario(snapshot, price_cents, horizon_end),
            )
            for price_cents in candidate_price_cents
        )

        return ForecastBundle(
            forecast_id=(
                f"{snapshot.snapshot_id}:simulated:"
                f"elasticity-{self.config.elasticity:.2f}:trend-{self.config.trend_per_interval:.2f}"
            ),
            snapshot_id=snapshot.snapshot_id,
            generated_at=snapshot.observed_at,
            model_name=self.config.model_name,
            model_version=self.config.model_version,
            scenarios=scenarios,
            assumption_notes=(
                f"base demand equals recent {snapshot.recent_window_minutes}-minute sales",
                f"price elasticity={self.config.elasticity:.2f}",
                f"trend per 30-minute interval={self.config.trend_per_interval:.2f}",
                "illustrative assumptions; not calibrated from merchant data",
            ),
        )

    def _forecast_price_scenario(
        self,
        snapshot: StoreSnapshot,
        price_cents: int,
        horizon_end: datetime,
    ) -> tuple[DemandInterval, ...]:
        intervals: list[DemandInterval] = []
        interval_start = snapshot.observed_at
        interval_index = 0

        # 最近30分钟销量作为当前价格下的基础需求率。
        base_demand = float(snapshot.recent_sales_units)
        price_ratio = price_cents / snapshot.current_price_cents
        # 价格降低时ratio小于1；负指数会产生大于1的需求乘数。
        price_multiplier = price_ratio ** (-self.config.elasticity)

        while interval_start < horizon_end:
            interval_end = min(
                interval_start + timedelta(minutes=self.config.interval_minutes),
                horizon_end,
            )
            time_multiplier = max(
                self.config.minimum_time_multiplier,
                1.0 + self.config.trend_per_interval * interval_index,
            )
            expected_units = max(0.0, base_demand * price_multiplier * time_multiplier)

            # 用Poisson方差近似产生可解释的P10/P50/P90。
            # 这一步没有限制库存；实际销量限制由DP中的min(demand, stock)处理。
            standard_deviation = sqrt(expected_units)
            p10 = max(0.0, expected_units - 1.2816 * standard_deviation)
            p50 = expected_units
            p90 = expected_units + 1.2816 * standard_deviation

            intervals.append(
                DemandInterval(
                    start_at=interval_start,
                    end_at=interval_end,
                    expected_units=round(expected_units, 2),
                    p10_units=round(p10, 2),
                    p50_units=round(p50, 2),
                    p90_units=round(p90, 2),
                )
            )
            interval_start = interval_end
            interval_index += 1

        return tuple(intervals)

    @staticmethod
    def _validate_request(
        snapshot: StoreSnapshot,
        candidate_price_cents: tuple[int, ...],
        horizon_end: datetime,
    ) -> None:
        if not candidate_price_cents:
            raise ValueError("at least one candidate price is required")
        if horizon_end <= snapshot.observed_at:
            raise ValueError("horizon_end must be later than observed_at")
        if horizon_end > snapshot.closes_at:
            raise ValueError("the MVP forecast horizon cannot extend beyond closing")

        feasible = set(snapshot.feasible_price_cents())
        invalid = [price for price in candidate_price_cents if price not in feasible]
        if invalid:
            raise ValueError(f"candidate prices are not feasible: {invalid}")
