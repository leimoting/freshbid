from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from math import exp

from freshbid.models import (
    CandidateActionValue,
    ForecastBundle,
    PriceRecommendation,
    StoreSnapshot,
)


@dataclass(frozen=True)
class DynamicProgrammingConfig:
    """第一版DP使用的业务权重，单位均为港币分。"""

    # 打烊时每剩余一件商品所承担的惩罚，用于表达浪费或清货压力。
    leftover_penalty_cents_per_unit: int = 300
    # 每次更改价格产生的操作和顾客体验成本。
    price_change_penalty_cents: int = 50
    # 每小时决策一次；预测仍保留30分钟粒度。
    decision_interval_minutes: int = 60
    policy_name: str = "rolling-horizon-dp"
    policy_version: str = "0.1.0"

    def __post_init__(self) -> None:
        if self.leftover_penalty_cents_per_unit < 0:
            raise ValueError("leftover penalty cannot be negative")
        if self.price_change_penalty_cents < 0:
            raise ValueError("price change penalty cannot be negative")
        if self.decision_interval_minutes != 60:
            raise ValueError("the MVP currently supports hourly pricing decisions")


class RollingHorizonDP:
    """有限时域随机DP：计算到打烊的策略，只执行第一个小时动作。"""

    def __init__(self, config: DynamicProgrammingConfig | None = None) -> None:
        self.config = config or DynamicProgrammingConfig()

    def recommend(
        self,
        snapshot: StoreSnapshot,
        forecast: ForecastBundle,
    ) -> PriceRecommendation:
        self._validate_inputs(snapshot, forecast)

        scenario_by_price = {item.price_cents: item for item in forecast.scenarios}
        all_prices = tuple(sorted(scenario_by_price, reverse=True))
        interval_groups = self._make_hourly_groups(forecast)

        # 保存每个状态下的最优动作，之后用于计算最终库存分布。
        policy: dict[tuple[int, int, int], int] = {}

        def hourly_lambda(step_index: int, price_cents: int) -> float:
            scenario = scenario_by_price[price_cents]
            return sum(
                scenario.intervals[index].expected_units
                for index in interval_groups[step_index]
            )

        @lru_cache(maxsize=None)
        def value(step_index: int, inventory: int, current_price_cents: int) -> float:
            # 打烊后对剩余库存施加终值惩罚。
            if step_index == len(interval_groups):
                return -float(self.config.leftover_penalty_cents_per_unit * inventory)
            if inventory == 0:
                policy[(step_index, inventory, current_price_cents)] = current_price_cents
                return 0.0

            feasible_actions = tuple(price for price in all_prices if price <= current_price_cents)
            action_values: list[tuple[float, int]] = []
            for action_price in feasible_actions:
                expected_value = self._action_value(
                    step_index=step_index,
                    inventory=inventory,
                    current_price_cents=current_price_cents,
                    action_price_cents=action_price,
                    demand_lambda=hourly_lambda(step_index, action_price),
                    continuation=value,
                )
                action_values.append((expected_value, action_price))

            # 相同目标值时保留较高价格，减少不必要的降价。
            best_value, best_action = max(action_values, key=lambda item: (item[0], item[1]))
            policy[(step_index, inventory, current_price_cents)] = best_action
            return best_value

        initial_action_values: list[CandidateActionValue] = []
        for action_price in (price for price in all_prices if price <= snapshot.current_price_cents):
            action_value = self._action_value(
                step_index=0,
                inventory=snapshot.inventory_on_hand,
                current_price_cents=snapshot.current_price_cents,
                action_price_cents=action_price,
                demand_lambda=hourly_lambda(0, action_price),
                continuation=value,
            )
            initial_action_values.append(
                CandidateActionValue(
                    price_cents=action_price,
                    expected_total_objective_cents=round(action_value, 2),
                )
            )

        # 调用一次初始状态，确保最优动作写入policy字典。
        value(0, snapshot.inventory_on_hand, snapshot.current_price_cents)
        recommended_price = policy[(0, snapshot.inventory_on_hand, snapshot.current_price_cents)]

        first_hour_distribution = self._capped_poisson_sales_distribution(
            hourly_lambda(0, recommended_price), snapshot.inventory_on_hand
        )
        next_hour_expected_sales = sum(sales * probability for sales, probability in first_hour_distribution)
        next_hour_expected_revenue = recommended_price * next_hour_expected_sales

        closing_distribution = self._evaluate_policy_distribution(
            snapshot=snapshot,
            interval_groups=interval_groups,
            scenario_by_price=scenario_by_price,
            policy=policy,
            value_function=value,
        )
        expected_inventory_at_close = sum(
            inventory * probability
            for (inventory, _price), probability in closing_distribution.items()
        )
        sellout_probability = sum(
            probability
            for (inventory, _price), probability in closing_distribution.items()
            if inventory == 0
        )

        representative_path = self._representative_path(
            snapshot=snapshot,
            interval_groups=interval_groups,
            scenario_by_price=scenario_by_price,
            policy=policy,
            value_function=value,
        )

        reason_codes = ["MAX_EXPECTED_OBJECTIVE"]
        if recommended_price < snapshot.current_price_cents:
            reason_codes.append("MARKDOWN_RECOMMENDED")
        else:
            reason_codes.append("HOLD_CURRENT_PRICE")

        return PriceRecommendation(
            recommendation_id=f"{snapshot.snapshot_id}:dp:{self.config.policy_version}",
            snapshot_id=snapshot.snapshot_id,
            forecast_id=forecast.forecast_id,
            generated_at=snapshot.observed_at,
            policy_name=self.config.policy_name,
            policy_version=self.config.policy_version,
            recommended_price_cents=recommended_price,
            next_hour_expected_sales=round(next_hour_expected_sales, 2),
            next_hour_expected_revenue_cents=round(next_hour_expected_revenue, 2),
            sellout_probability_by_close=round(sellout_probability, 4),
            expected_inventory_at_close=round(expected_inventory_at_close, 2),
            representative_price_path_cents=representative_path,
            candidate_action_values=tuple(initial_action_values),
            reason_codes=tuple(reason_codes),
        )

    def _action_value(
        self,
        *,
        step_index: int,
        inventory: int,
        current_price_cents: int,
        action_price_cents: int,
        demand_lambda: float,
        continuation,
    ) -> float:
        change_penalty = (
            self.config.price_change_penalty_cents
            if action_price_cents != current_price_cents
            else 0
        )
        expected_value = -float(change_penalty)
        for sales, probability in self._capped_poisson_sales_distribution(demand_lambda, inventory):
            next_inventory = inventory - sales
            immediate_revenue = action_price_cents * sales
            expected_value += probability * (
                immediate_revenue
                + continuation(step_index + 1, next_inventory, action_price_cents)
            )
        return expected_value

    def _evaluate_policy_distribution(
        self,
        *,
        snapshot: StoreSnapshot,
        interval_groups: tuple[tuple[int, ...], ...],
        scenario_by_price,
        policy: dict[tuple[int, int, int], int],
        value_function,
    ) -> dict[tuple[int, int], float]:
        states: dict[tuple[int, int], float] = {
            (snapshot.inventory_on_hand, snapshot.current_price_cents): 1.0
        }
        for step_index, group in enumerate(interval_groups):
            next_states: defaultdict[tuple[int, int], float] = defaultdict(float)
            for (inventory, current_price), state_probability in states.items():
                value_function(step_index, inventory, current_price)
                action_price = policy[(step_index, inventory, current_price)]
                demand_lambda = sum(
                    scenario_by_price[action_price].intervals[index].expected_units
                    for index in group
                )
                for sales, demand_probability in self._capped_poisson_sales_distribution(
                    demand_lambda, inventory
                ):
                    next_states[(inventory - sales, action_price)] += (
                        state_probability * demand_probability
                    )
            states = dict(next_states)
        return states

    def _representative_path(
        self,
        *,
        snapshot: StoreSnapshot,
        interval_groups: tuple[tuple[int, ...], ...],
        scenario_by_price,
        policy: dict[tuple[int, int, int], int],
        value_function,
    ) -> tuple[int, ...]:
        """沿每小时最接近期望销量的库存状态生成一条便于展示的代表路径。"""

        inventory = snapshot.inventory_on_hand
        current_price = snapshot.current_price_cents
        path: list[int] = []
        for step_index, group in enumerate(interval_groups):
            value_function(step_index, inventory, current_price)
            action_price = policy[(step_index, inventory, current_price)]
            path.append(action_price)
            demand_lambda = sum(
                scenario_by_price[action_price].intervals[index].expected_units
                for index in group
            )
            distribution = self._capped_poisson_sales_distribution(demand_lambda, inventory)
            expected_sales = sum(sales * probability for sales, probability in distribution)
            inventory = max(0, inventory - round(expected_sales))
            current_price = action_price
        return tuple(path)

    @staticmethod
    def _capped_poisson_sales_distribution(
        demand_lambda: float,
        inventory: int,
    ) -> tuple[tuple[int, float], ...]:
        """把Poisson需求转换成实际销量分布，并把超出库存的概率合并为售罄。"""

        if inventory == 0:
            return ((0, 1.0),)
        if demand_lambda <= 0:
            return ((0, 1.0),)

        outcomes: list[tuple[int, float]] = []
        probability = exp(-demand_lambda)
        cumulative_probability = 0.0
        for demand in range(inventory):
            if demand > 0:
                probability *= demand_lambda / demand
            outcomes.append((demand, probability))
            cumulative_probability += probability

        # 所有需求大于等于库存的情况，实际销量都等于当前库存。
        sellout_probability = max(0.0, 1.0 - cumulative_probability)
        outcomes.append((inventory, sellout_probability))
        total_probability = sum(item[1] for item in outcomes)
        return tuple((sales, probability / total_probability) for sales, probability in outcomes)

    def _make_hourly_groups(self, forecast: ForecastBundle) -> tuple[tuple[int, ...], ...]:
        interval_count = len(forecast.scenarios[0].intervals)
        intervals_per_decision = self.config.decision_interval_minutes // forecast.interval_minutes
        return tuple(
            tuple(range(start, min(start + intervals_per_decision, interval_count)))
            for start in range(0, interval_count, intervals_per_decision)
        )

    @staticmethod
    def _validate_inputs(snapshot: StoreSnapshot, forecast: ForecastBundle) -> None:
        if forecast.snapshot_id != snapshot.snapshot_id:
            raise ValueError("forecast does not belong to this store snapshot")
        if not forecast.scenarios:
            raise ValueError("forecast must contain price scenarios")

        feasible_prices = set(snapshot.feasible_price_cents())
        scenario_prices = {item.price_cents for item in forecast.scenarios}
        if scenario_prices != feasible_prices:
            raise ValueError("forecast scenarios must match all feasible merchant price tiers")

        reference_intervals = tuple(
            (item.start_at, item.end_at) for item in forecast.scenarios[0].intervals
        )
        if not reference_intervals:
            raise ValueError("forecast scenarios cannot be empty")
        for scenario in forecast.scenarios[1:]:
            intervals = tuple((item.start_at, item.end_at) for item in scenario.intervals)
            if intervals != reference_intervals:
                raise ValueError("all price scenarios must use identical forecast intervals")
