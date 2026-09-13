from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


# 常用字段类型：在数据进入预测或定价模块前，先阻止负库存、负销量等无效值。
NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveInt = Annotated[int, Field(gt=0)]
NonNegativeFloat = Annotated[float, Field(ge=0)]
Probability = Annotated[float, Field(ge=0, le=1)]


class ContractModel(BaseModel):
    # 禁止未声明字段，避免不同模块因为字段拼写错误而悄悄产生不一致的数据。
    # frozen=True 让一次观测记录不可变；状态变化应创建一条新记录并保留历史。
    model_config = ConfigDict(extra="forbid", frozen=True)


class StoreSnapshot(ContractModel):
    """某一时刻的门店状态，同时作为需求预测和定价模块的统一输入。"""

    snapshot_id: str
    store_id: str
    product_id: str
    product_name: str
    observed_at: datetime
    closes_at: datetime
    currency: Literal["HKD"] = "HKD"
    # 金额统一使用“分”存储。例如 2400 表示 HK$24.00，以避免浮点金额误差。
    original_price_cents: PositiveInt
    current_price_cents: PositiveInt
    # 商家预先批准的价格档位，必须按照从高到低排列。
    approved_price_cents: tuple[PositiveInt, ...]
    inventory_on_hand: NonNegativeInt
    recent_sales_units: NonNegativeInt
    recent_window_minutes: PositiveInt = 30
    # 客流不是所有门店都能提供，因此允许为空；核心流程不能强依赖客流数据。
    recent_footfall: NonNegativeInt | None = None
    is_holiday: bool = False
    markdown_started: bool = False
    # 门店运营约束：即使系统每小时计算，也可以限制每天真正改价的次数。
    last_price_change_at: datetime | None = None
    price_changes_today: NonNegativeInt = 0
    maximum_price_changes_per_day: PositiveInt = 2

    @model_validator(mode="after")
    def validate_snapshot(self) -> StoreSnapshot:
        """检查门店状态内部是否一致，失败时阻止数据继续进入下游模块。"""
        if self.closes_at <= self.observed_at:
            raise ValueError("closes_at must be later than observed_at")
        if self.current_price_cents > self.original_price_cents:
            raise ValueError("current price cannot exceed original price")
        if len(set(self.approved_price_cents)) != len(self.approved_price_cents):
            raise ValueError("approved prices must be unique")
        if tuple(sorted(self.approved_price_cents, reverse=True)) != self.approved_price_cents:
            raise ValueError("approved prices must be ordered from highest to lowest")
        if any(price > self.original_price_cents for price in self.approved_price_cents):
            raise ValueError("approved prices cannot exceed original price")
        if self.last_price_change_at is not None:
            if self.last_price_change_at > self.observed_at:
                raise ValueError("last price change cannot occur after the snapshot")
        return self

    def feasible_price_cents(self) -> tuple[int, ...]:
        """返回本次决策允许选择的价格，落实“降价后只可保持或继续降低”。"""
        # 只保留不高于当前价格的商家批准档位，因此不会出现反向涨价。
        prices = tuple(price for price in self.approved_price_cents if price <= self.current_price_cents)
        # 降价期尚未开始时，允许把当前价格作为“保持原价”动作加入候选集合。
        if not self.markdown_started and self.current_price_cents not in prices:
            prices = (self.current_price_cents, *prices)
        # 保持原有顺序，同时避免当前价格与批准档位重复。
        return tuple(dict.fromkeys(prices))


class DemandInterval(ContractModel):
    """一个30分钟区间内的概率需求预测。"""
    start_at: datetime
    end_at: datetime
    expected_units: NonNegativeFloat
    p10_units: NonNegativeFloat
    p50_units: NonNegativeFloat
    p90_units: NonNegativeFloat

    @model_validator(mode="after")
    def validate_interval(self) -> DemandInterval:
        """确保预测区间有效，而且分位数从低到高排列。"""
        if self.end_at <= self.start_at:
            raise ValueError("end_at must be later than start_at")
        if not self.p10_units <= self.p50_units <= self.p90_units:
            raise ValueError("forecast quantiles must satisfy p10 <= p50 <= p90")
        return self


class PriceScenarioForecast(ContractModel):
    """在一个指定候选价格下，从当前时刻到打烊的需求预测序列。"""
    price_cents: PositiveInt
    intervals: tuple[DemandInterval, ...]


class ForecastBundle(ContractModel):
    """一次预测调用的完整输出，包含每个候选价格对应的需求分布。"""
    forecast_id: str
    snapshot_id: str
    generated_at: datetime
    model_name: str
    model_version: str
    interval_minutes: Literal[30] = 30
    scenarios: tuple[PriceScenarioForecast, ...]
    # 记录模拟或模型假设，避免把Demo预测误认为真实门店结论。
    assumption_notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_scenarios(self) -> ForecastBundle:
        """DP至少需要一个价格场景，而且每个候选价格只能出现一次。"""
        prices = [scenario.price_cents for scenario in self.scenarios]
        if not prices:
            raise ValueError("at least one price scenario is required")
        if len(set(prices)) != len(prices):
            raise ValueError("forecast scenarios must use unique prices")
        return self


class CandidateActionValue(ContractModel):
    """初始状态下选择某个价格所得到的DP目标值。"""

    price_cents: PositiveInt
    expected_total_objective_cents: float


class PriceRecommendation(ContractModel):
    """定价策略的统一输出；未来DP或Bandit都必须返回相同结构。"""
    recommendation_id: str
    snapshot_id: str
    forecast_id: str
    generated_at: datetime
    policy_name: str
    policy_version: str
    recommended_price_cents: PositiveInt
    next_hour_expected_sales: NonNegativeFloat
    next_hour_expected_revenue_cents: NonNegativeFloat
    sellout_probability_by_close: Probability
    expected_inventory_at_close: NonNegativeFloat
    # DP策略取决于未来实际销量；这里保存沿期望库存状态得到的展示路径。
    representative_price_path_cents: tuple[PositiveInt, ...]
    candidate_action_values: tuple[CandidateActionValue, ...]
    reason_codes: tuple[str, ...]


class SafetyResult(ContractModel):
    """确定性安全检查的结果；LLM Agent不参与通过或拒绝价格。"""
    recommendation_id: str
    checked_at: datetime
    passed: bool
    violation_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_result(self) -> SafetyResult:
        if self.passed and self.violation_codes:
            raise ValueError("a passing safety result cannot contain violations")
        if not self.passed and not self.violation_codes:
            raise ValueError("a failed safety result must contain at least one violation")
        return self


class DecisionEventType(StrEnum):
    # 分开记录各阶段，避免把“商家同意”误认为“门店价格已经生效”。
    RECOMMENDED = "recommended"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"
    VERIFIED = "verified"


class DecisionEvent(ContractModel):
    """推荐从生成到实际生效的状态事件。"""
    recommendation_id: str
    event_type: DecisionEventType
    occurred_at: datetime
    actor: str
    effective_price_cents: PositiveInt | None = None

    @model_validator(mode="after")
    def validate_effective_price(self) -> DecisionEvent:
        # 应用和验证事件必须记录实际价格，才能把后续销量归因到正确价格。
        if self.event_type in {DecisionEventType.APPLIED, DecisionEventType.VERIFIED}:
            if self.effective_price_cents is None:
                raise ValueError("applied and verified events require an effective price")
        return self


class OutcomeRecord(ContractModel):
    """一个实际销售区间的反馈记录，供评估和后续模型训练使用。"""
    recommendation_id: str
    interval_start: datetime
    interval_end: datetime
    effective_price_cents: PositiveInt
    units_sold: NonNegativeInt
    ending_inventory: NonNegativeInt
    policy_version: str
    model_version: str
    # 明确区分真实观测和Demo模拟，避免后续训练误用模拟记录。
    source: Literal["observed", "simulated"] = "observed"

    @model_validator(mode="after")
    def validate_period(self) -> OutcomeRecord:
        if self.interval_end <= self.interval_start:
            raise ValueError("interval_end must be later than interval_start")
        return self
