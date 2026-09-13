# FreshBid

FreshBid 是一个面向面包店及其他易腐食品商家的动态降价决策项目。系统根据近期销量、当前库存、剩余营业时间和商家约束预测不同价格下的需求，再给出以**提高收入并减少打烊剩余库存**为目标的降价建议。

当前仓库包含一个可以在本地浏览器中操作的模块化 MVP。它用于验证数据接口、需求预测、动态规划、安全检查、商家确认和反馈留档能否完整跑通，不代表已经经过真实门店数据验证的商业系统。

## 1. 项目目标与边界

当前目标：

- 面向面包、便当、寿司等当日易腐商品；第一阶段聚焦面包店。
- 每小时重新计算一次建议价格。
- 系统只提供建议，必须由店主或经理确认。
- 晚间降价开始后，价格只能保持或继续下降，不能重新上涨。
- 同时考虑收入与剩余库存；暂时不决定生产或补货数量。
- 所有推荐、批准、执行、验证和结果均需留档。

暂不负责：

- 自动决定每天生产多少商品。
- 未经商家批准自动修改价格。
- 直接连接真实 POS、电子价签或支付系统。
- 将 Demo 中的模拟预测宣传为真实经营效果。

## 2. 完整工作栈（长期架构）

完整方案采用可替换模块，目标是后续升级模型时不重写整体系统。

```text
POS / 库存 / 客流 / 时间日历 / 天气等外部信息
                         │
                         ▼
              数据接入、校验与特征工程
                         │
       ┌─────────────────┴─────────────────┐
       ▼                                   ▼
商家偏好 Agent                      概率需求预测模块
自然语言约束 → 结构化规则       ARIMA/LSTM 基线 → Transformer
       │                           每价格、每时段需求分布
       └─────────────────┬─────────────────┘
                         ▼
                 动态定价决策模块
          初期：滚动时域 DP（每小时一次）
          后期：Contextual Bandit 安全探索
                         │
                         ▼
                  确定性安全检查
       价格档位 / 价格底线 / 只降不升 / 调价频率
                         │
                         ▼
                定价解释与复核 Agent
        解释原因、提示异常；不得覆盖价格数值
                         │
                         ▼
              商家确认 UI → 应用价格 → 验证
                         │
                         ▼
          实际销量、收入、剩余库存和浪费反馈
                         │
                         └──────► 评估、再训练与监控
```

### 2.1 数据与特征层

完整版本计划输入：

- 过去 30 分钟进店人数。
- 过去 30 分钟商品销量。
- 当前时间、星期、节假日。
- 商品名称、类别、原价、当前价。
- 当前库存、临期时间、打烊时间。
- 商家允许的价格档位、最低价格和调价频率。
- 后续可加入天气、周边活动和历史促销信息。

数据来源可以是门店 POS/库存系统、线下调研、Kaggle 或付费零售数据。外部数据只用于冷启动与模拟，必须注意它与香港单店面包销售之间的领域偏移。

### 2.2 需求预测层

计划采用分阶段方案：

1. 数据较少时先使用规则、ARIMA 或 LSTM 作为基线。
2. 数据量和质量足够后，使用时间序列 Transformer 并进行微调。
3. 针对每个合法价格和每个 30 分钟区间输出概率预测，而不只输出一个点估计。
4. 统一输出 `expected / P10 / P50 / P90`，保证预测模型可以被替换而不影响定价模块。

Transformer 属于核心 AI 预测能力。Agent 不直接代替预测模型，也不直接决定价格。

### 2.3 动态定价层

第一版使用滚动时域动态规划（Dynamic Programming, DP）：

- 状态：时间、剩余库存、当前价格。
- 动作：商家提前批准且不高于当前价格的离散价格档位。
- 收益：销售收入。
- 成本：打烊剩余库存惩罚、调价操作成本。
- 频率：每小时重新计算，只执行当前第一个动作。

后续使用 Contextual Bandit 做受控探索，以减少“只能观察已执行价格销量”带来的选择性偏差。DPO/RLHF 暂不作为第一版数值定价方法。

### 2.4 双 Agent 层

完整方案保留两个职责独立的 Agent：

- **Merchant Preference Agent**：把店主的自然语言要求转换为结构化约束，例如最低折扣、允许时段和每日最大调价次数。
- **Pricing Review / Explanation Agent**：将预测和 DP 结果转换为商家能理解的解释，并提示异常或风险。

两个 Agent 均无权直接覆盖 DP 价格。所有价格必须经过确定性安全规则，避免 LLM 的随机性进入财务敏感决策链。

### 2.5 完整技术栈

| 层级 | 计划技术 | 作用 |
|---|---|---|
| 商家界面 | React、JavaScript | 查看建议、确认或拒绝、录入结果 |
| API 与业务编排 | Python、FastAPI、Pydantic | 串联模型、定价、安全规则和状态流 |
| 需求预测 | Python、ARIMA/LSTM、时间序列 Transformer | 输出价格条件概率需求预测 |
| 定价优化 | Python、动态规划；后续 Contextual Bandit | 选择合法价格并优化经营目标 |
| Agent | 后续接入 Qwen 等模型 | 约束结构化、解释和异常复核 |
| 数据存储 | 当前 SQLite；后续 PostgreSQL/时序存储 | 保存经营状态、决策事件和实际结果 |
| POS 适配 | 视门店接口采用 Python、JavaScript 或 C# | 获取销售/库存并应用价格 |
| 训练环境 | 本地开发，单台 RTX 5080 | 微调、离线评估和模型推理实验 |

生产量优化已在架构中留出接口，但不属于当前定价 MVP。

## 3. Demo 阉割后的工作栈（当前已实现）

为保证短期内可以演示，当前版本只实现最小闭环：

```text
网页手动输入门店快照
        │
        ▼
模拟价格条件需求预测
近期销量 + 假设价格弹性 + 时间趋势
        │
        ▼
滚动时域随机 DP
比较 HK$24.00 / HK$21.60 / HK$19.20
        │
        ▼
确定性安全检查
        │
        ▼
商家批准 / 拒绝 → 应用 → 货架价格验证
        │
        ▼
填写模拟销量与剩余库存 → SQLite 留档
```

当前模块：

- React 商家确认页面。
- FastAPI 接口。
- Pydantic 数据契约。
- 30 分钟粒度的模拟概率需求预测。
- 每小时决策一次的随机 DP。
- 商家批准价格档位。
- 只降不升、价格底线、推荐有效期、调价间隔和每日调价次数检查。
- `recommended → approved/rejected → applied → verified` 状态机。
- SQLite 决策事件和结果留档。

当前没有实现：

- 真实 ARIMA、LSTM 或 Transformer 训练。
- 商家偏好 Agent 和解释 Agent。
- 真实 POS、库存系统或电子价签连接。
- Contextual Bandit 探索。
- 用户登录、权限、多门店和云端部署。
- 生产数量联合优化。

## 4. Demo 当前计算逻辑

### 4.1 模拟需求预测

当前预测器以最近 30 分钟销量作为基础需求：

```text
候选价格需求 = 基础需求 × (候选价 / 当前价)^(-价格弹性) × 时间趋势
```

默认价格弹性为 `1.00`，每经过一个 30 分钟区间，需求趋势下降 `8%`。预测返回：

- `P10`：真实需求约有 10% 概率低于该数值，代表偏保守情形。
- `P50`：需求分布中位数；当前 Demo 与期望值相同。
- `P90`：真实需求约有 90% 概率低于该数值，代表较高需求情形。

这些参数只是为了打通 Demo，尚未使用真实门店数据校准。

### 4.2 DP 定价

DP 假设每个区间需求服从以预测均值为参数的 Poisson 分布：

```text
目标值 = 预期销售收入
       - 打烊剩余库存惩罚
       - 改价操作惩罚
```

系统计算从当前时刻到打烊的价格策略，但每次只建议并执行下一个小时的价格。默认参数：

- 剩余一件商品的惩罚：HK$3.00。
- 每次改价的操作惩罚：HK$0.50。
- 决策频率：60 分钟。
- 预测间隔：30 分钟。

### 4.3 安全与留档

价格必须通过以下硬规则：

- 属于商家批准的档位。
- 晚间降价后不得上涨。
- 不低于商家批准的最低档位。
- 推荐没有过期。
- 满足最短调价间隔。
- 没有超过每日最大调价次数。

生命周期中的每一步单独记录，避免把“商家点击批准”误认为“货架价格已经生效”。Demo 反馈使用 `source=simulated`，防止未来训练时误当真实数据。

## 5. 环境要求

推荐环境：

- Windows 10/11 和 PowerShell。
- Python 3.11 或更高版本。
- Node.js 20 或更高版本。
- pnpm 9 或更高版本。

检查版本：

```powershell
python --version
node --version
pnpm --version
```

如果 Node.js 已安装但没有 pnpm，可以尝试：

```powershell
corepack enable
corepack prepare pnpm@latest --activate
```

如果当前 Node.js 不包含 Corepack，可使用带 npm 的 Node.js 安装包，再执行：

```powershell
npm install --global pnpm
```

## 6. 首次安装

在 PowerShell 中执行：

```powershell
cd C:\竞赛\freshbid

# 建立独立Python环境，避免影响系统中其他项目。
python -m venv .venv

# 无需激活环境，直接使用该环境的Python安装后端。
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .\backend

# 安装并构建React前端。
cd .\frontend
pnpm install
pnpm build
cd ..
```

如果仅需要在当前 Python 环境中快速运行，也可以执行：

```powershell
cd C:\竞赛\freshbid\backend
python -m pip install -e .
```

## 7. 启动浏览器 Demo

完成首次安装后，在项目根目录运行：

```powershell
cd C:\竞赛\freshbid
.\run_demo.ps1
```

启动后访问：

- 商家界面：<http://127.0.0.1:8000>
- API 文档：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/api/health>

按 `Ctrl+C` 停止服务器。

也可以不使用启动脚本：

```powershell
cd C:\竞赛\freshbid
.\.venv\Scripts\python.exe -m freshbid.api
```

前端开发模式需要两个终端：

```powershell
# 终端一：后端
cd C:\竞赛\freshbid
.\.venv\Scripts\python.exe -m freshbid.api

# 终端二：前端热更新
cd C:\竞赛\freshbid\frontend
pnpm dev
```

开发页面地址为 <http://127.0.0.1:5173>，Vite 会把 `/api` 请求转发至 FastAPI。

## 8. 浏览器操作流程

1. 输入商品名称、库存、最近 30 分钟销量、客流和距离打烊时间。
2. 点击 **Generate recommendation**。
3. 查看建议价格、预计销量、预计收入、售罄概率和预计打烊库存。
4. 查看不同价格的 DP 目标值和 30 分钟概率预测。
5. 确认所有安全规则通过。
6. 点击 **Approve recommendation**，或选择 **Reject**。
7. 确认门店已经改价后点击 **Confirm price applied**。
8. 检查货架价格后点击 **Verify shelf price**。
9. 输入下一小时销量和剩余库存，点击 **Save feedback**。

## 9. 运行命令行 Demo

命令行版本会自动模拟批准、执行、验证和结果反馈：

```powershell
cd C:\竞赛\freshbid
.\.venv\Scripts\python.exe -m freshbid.demo
```

如果后端尚未以可编辑模式安装，可临时指定源码目录：

```powershell
cd C:\竞赛\freshbid\backend
$env:PYTHONPATH = "src"
python -m freshbid.demo
```

## 10. 测试

```powershell
cd C:\竞赛\freshbid
.\.venv\Scripts\python.exe -m unittest discover -s .\backend\tests -v
```

测试覆盖：

- 数据契约校验。
- 合法价格筛选和只降不升。
- 概率预测输出。
- DP 推荐及概率指标。
- 安全规则。
- 生命周期非法跳转拦截。
- SQLite 留档。
- HTTP API 完整流程。

## 11. API 概览

| 方法 | 路径 | 功能 |
|---|---|---|
| `GET` | `/api/health` | 服务健康检查 |
| `POST` | `/api/recommendations` | 创建预测和定价建议 |
| `GET` | `/api/recommendations/{id}` | 查看建议、事件和结果 |
| `POST` | `/api/recommendations/{id}/approve` | 商家批准建议 |
| `POST` | `/api/recommendations/{id}/reject` | 商家拒绝建议 |
| `POST` | `/api/recommendations/{id}/apply` | 确认价格已应用 |
| `POST` | `/api/recommendations/{id}/verify` | 验证实际货架价格 |
| `POST` | `/api/recommendations/{id}/outcomes` | 保存销量和库存反馈 |

## 12. 项目目录

```text
freshbid/
├─ backend/
│  ├─ src/freshbid/
│  │  ├─ api.py                    # FastAPI 路由
│  │  ├─ service.py                # 应用编排层
│  │  ├─ models.py                 # Pydantic 数据契约
│  │  ├─ forecasting/simulated.py  # 当前模拟预测器
│  │  ├─ optimization/             # 滚动时域 DP
│  │  ├─ safety/                   # 确定性安全规则
│  │  ├─ lifecycle.py              # 推荐状态机
│  │  └─ storage/sqlite.py         # SQLite 留档
│  ├─ tests/                       # 自动测试
│  └─ data/                        # 本地数据库
├─ frontend/
│  ├─ src/main.jsx                 # React 商家页面
│  ├─ src/styles.css               # 页面样式
│  └─ dist/                        # 构建后由 FastAPI 提供
├─ run_demo.ps1                    # 一键启动脚本
└─ README.md
```

## 13. 当前限制与风险

- 预测数据为模拟数据，任何收入或浪费改善数字都不能视为实验结论。
- 当前只支持单次建议中的一个门店和一个商品。
- 页面输入仍是人工快照，不是实时 POS 数据。
- API 会话暂存在内存中；服务器重启后需要新建推荐。SQLite 中的事件和结果仍会保留。
- 离散三档价格适合快速验证，但不代表已证明复杂模型优于简单规则。
- 实体门店频繁换价存在操作成本和顾客公平感知问题，因此保留调价频率限制和人工确认。
- 只观察实际执行价格下的销量会产生选择性偏差，后续需要安全探索或实验设计。

## 14. 后续开发顺序

建议按照以下顺序迭代：

1. 完成门店访谈和线下数据字段确认。
2. 使用历史或模拟数据建立 ARIMA/LSTM 基线并与当前规则模型比较。
3. 增加离线回测：收入、剩余库存、浪费、售罄率和预测误差。
4. 把真实预测器接入现有 `ForecastBundle` 接口。
5. 增加数据库中的快照、预测和推荐持久化，支持服务重启恢复。
6. 加入多商品、多门店和账户权限。
7. 接入 Merchant Preference Agent 和 Pricing Explanation Agent。
8. 在硬安全约束内试验 Contextual Bandit。
9. 根据门店条件接入 POS 或电子价签。
10. 后续再研究定价与生产数量的联合优化。

## 15. 线下调研建议字段

为下一阶段建模，建议组员优先采集或询问：

- 每种商品的原价、折扣档位和实际开始折扣时间。
- 一天是否允许多次改价，以及员工执行改价需要多久。
- 每 30 分钟销量、客流和剩余库存能否获得。
- 每日生产量、补货批次、打烊剩余量和处理方式。
- 商家最看重收入、售罄、浪费还是操作简单。
- 可接受的最低售价、最大折扣和每日最大调价次数。
- 是否有 POS 导出、库存接口或电子价签。
- 顾客是否会因同日多次降价产生投诉或等待心理。

访谈记录必须区分事实、店主观点和团队假设；在获得真实数据前，PPT 与 Demo 应继续明确标注 `illustrative`、`simulated` 或 `TO DO`。
