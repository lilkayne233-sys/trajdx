# trajdx

**面向 Code Agent 轨迹的步骤级失败诊断工具。**

语言：[中文](README.md) · [English](README.en.md)

SWE-bench 这类评测对每个任务只给一个二值结果——resolved 或 not。这个分数能告诉你
agent *失败了*，却永远不会告诉你它失败在*哪一步*、*为什么失败*。`trajdx` 把原始
agent 日志归一化成事件序列，在序列上跑纯 Python 规则检测器，并报出 agent 陷入循环、
盲目搜索、或停止自我验证的具体步骤区间。

检测链路里没有大模型，不联网，不需要 Docker。单条轨迹检测耗时约 3 ms。

---

## 整体流程

| 阶段 | 模块 | 产出 |
|---|---|---|
| 归一化 | `trajdx.adapters` | 由 `AgentStep` 组成的 `Trajectory` |
| 报错指纹 | `trajdx.fingerprints` | 24 类错误 + exit code |
| 规则检测 | `trajdx.detectors` | 带步骤区间和证据的 `Finding` |
| 浪费量化 | `trajdx.metrics` | Wasted Step Ratio 与分类归因 |
| 渲染输出 | `trajdx.report`、`trajdx.cli` | replay / diagnose / export / findings |

一个 `AgentStep` 就是一组「决策–动作–观测」三元组。每个 step 带三个身份键：

- `action_key` —— agent *意图*做什么，其中易变 token（路径、id、行号）已被抹平，
  于是 `pytest tests/test_x.py` 和 `pytest tests/test_y.py` 会归并到一起。
- `exact_key` —— 含载荷的字面动作，所以同一文件上的两次 edit，只有替换文本也一致
  才算「同一个动作」。
- `observation_key` —— 对*结果*的身份标识，对整段清洗后的观测体做哈希。

`action_key` 与 `exact_key` 之间的差，正是循环检测能保持诚实的原因：**重复一个动作
并不等于失败，前提是 agent 在两次之间改动了输入。**

---

## 快速上手

```bash
python -m trajdx.cli detectors                       # 有哪些规则
python -m trajdx.cli adapters                        # 支持哪些日志格式
python -m trajdx.cli replay data/raw/openhands_sample.jsonl --index 0
python -m trajdx.cli diagnose data/raw/openhands_sample.jsonl
python -m trajdx.cli export data/raw/openhands_sample.jsonl --out data/reports/diag.jsonl
python -m trajdx.cli findings data/raw/openhands_sample.jsonl --out data/labels/to_label.jsonl
```

开发环境安装：

```bash
pip install -e ".[dev]"
pytest -q
```

---

## 检测器与实测精确率

规则基于 300 条真实 OpenHands 轨迹（150 resolved / 150 unresolved）构建，并经多轮
「大模型预标注 + 人工复核」的 finding 校准。下表**不是手写的**，而是由下面这条命令
直接生成，可原样复现：

```bash
python scripts/evaluate.py --findings data/labels/to_label_v3.jsonl \
  --labels "data/labels/labelled_v3_*.jsonl" --markdown
```

| 检测器 | 层级 | Precision | n |
|---|---|---|---|
| `termination_anomaly` | core | 91.7% | 24 |
| `blind_search` | experimental | 80.0% | 5 |
| `verification_gap` | experimental | 53.8% | 13 |
| `weak_verification` | experimental | 18.2% | 11 |
| `execution_loop` | experimental | 10.0% | 30 |
| `environment_stuck` | experimental | — | 0 |
| `localization_failure` | experimental | — | 0 |
| `redundant_read` | experimental | — | 0 |
| **整体** | | **45.8%** | **83** |

读表前先看 `n`：**`n=0`（显示为「—」）表示这条规则在该轮没有命中样本，而不是准确率
100%，两者绝不能混读。** `redundant_read` 正是这种情形：它在未随仓库发布的轮次里拿到过
4/4，但仓库内的 v3 轮一条样本都没有，因此在仓库内它既没被证实、也没被证伪——层级只能
停留在 `experimental`，而不是 core。同样，仓库内可复现的人工结论是 v2 与 v3 两轮的
**204 条**（覆盖 88 条轨迹），更早与更晚轮次的复核结果没有随仓库发布。

**在这批可复现的标注里，只有 `termination_anomaly`（91.7%）越过了 88% 这条线。**
其余规则都照常随包发布，但被标记为 `Tier.EXPERIMENTAL`，并且 `replay` / `findings`
默认不输出 experimental 规则——单条 experimental finding 不应被当作结论来读。需要
样本量做聚合分析时用 `--tier all`，聚合表会同时披露各分类的区分度。

这是标注闭环跑出来的**诚实结果，不是一个被达成的目标**。命中假设的规则被保留并提层，
其余的被标为「未证实」，而不是悄悄调到数字好看为止。

这张表和 88% 这条线**只适用于标注类规则**。`localization_failure` 走的是另一条通道
（把 agent 的 patch 与 gold patch 对照），而本仓库发布的标注轮次里没有 gold，所以它在
上表里永远是 `n=0`。它的质量由下面「gold 通道」一节的区分度实测背书，而不是由 precision
背书——这两件事不能互相折算。

### 单条规则的说明

- **`execution_loop`** 有一个很尖锐的失效模式：*中间夹了编辑*的循环，有效率只有
  **6.9%（2/29）**；*中间没有编辑*的循环在这一轮只有 **1 例**（恰好有效），样本量
  不足以支持任何结论。加上「中间无编辑」的门槛后，全语料命中量从 **136 条降到
  5 条**（`require_no_intervening_edit` 开关的实测差异）——这也是它现在很少触发的
  原因。**改动输入之后再重复动作，通常属于合法的调试行为。**
- **`localization_failure`** 把 agent 的 patch 与 gold patch 的文件集合做比较。
  缺少 `meta["gold_files"]` 时它按设计保持沉默，而不是用启发式去猜。要启用它：

  ```bash
  python scripts/fetch_gold.py            # 从任务数据集取 gold 文件集合，写成旁挂文件
  python -m trajdx.cli diagnose data/raw/openhands_sample.jsonl \
    --gold data/gold/swe-rebench-gold.jsonl
  ```

  若 `huggingface.co` 连不上（超时、SSL 中断、分片卡在 0 字节），加
  `--mirror https://hf-mirror.com`；脚本按条落盘，中断后重跑会自动跳过已取到的。

  轨迹日志本身不含 gold patch——它只存在于任务数据集（SWE-rebench）里，所以这是必须
  外部提供的一路输入。诊断时会打印覆盖率，因为「旁挂文件没匹配上」与「规则确实没发现
  问题」在输出里长得一模一样。`--gold` 对 `replay` / `diagnose` / `export` / `findings`
  都可用。

  **gold 通道接通后的实测结果**（297/297 个 `instance_id` 全部取到，覆盖 300/300 条轨迹，
  覆盖率 **100%**）：

  | | resolved | unresolved | Δ |
  |---|---|---|---|
  | `termination_anomaly` | 5.3%（8/150） | 20.7%（31/150） | +15.3 |
  | `localization_failure` | 3.3%（5/150） | **18.0%（27/150）** | **+14.7** |

  按「命中率」计（一条轨迹命中多条同类 finding 只算一次）。`localization_failure` 是
  全表中区分度**第二高**的规则，仅次于 `termination_anomaly`；它也是被标出的浪费步数的
  主要来源——`diagnose` 里 1.6 步平均浪费中有 1.4 步来自它。层级仍停在 `experimental`：
  88% 那条线是按「标注精确率」定义的，而这条规则无法用标注验证（标注里没有 gold），
  换一把尺子把它提进 core 会让 core 的含义失效。它是目前唯一一条有外部真值背书的规则。
- **`weak_verification`** 与 **`verification_gap`** 计入的浪费步骤数为 **0**。
  覆盖缺口是一个诊断信号，不等于这些步骤本身被浪费了。

---

## Wasted Step Ratio（浪费步数占比）

`WSR` 按严重度把每个被标记的步骤**恰好归入一个**浪费分类，因此恒有 `WSR <= 1.0`，
且没有任何步骤被重复计费。

```bash
python scripts/detector_profile.py     # 各检测器的命中量与步骤覆盖率
python scripts/discrimination.py       # 各指标对 resolved 标签的 AUC
```

**WSR 不能预测失败。** 对 resolved/unresolved 标签做 Mann-Whitney AUC 只有
**0.520**——与抛硬币无异。把它当作失败预测器会是错的，所以文档和 CLI 都严格把它
描述成一个*效率*指标：它说明一次运行有多少比例在原地打转，而不是这次运行会不会成功。

真正携带信号的是「过程形状」类指标。下表同样由 `scripts/discrimination.py` 直接输出
（AUC 一列的正类是 *失败*，所以小于 0.5 表示「数值越低越容易失败」，反向后就大于 0.5）：

| 指标 | AUC（正类=失败） | 反向 | Resolved | Unresolved |
|---|---|---|---|---|
| `total_steps` | 0.694 | — | 58.83 | 71.41 |
| `source_edits` | 0.632 | — | 2.97 | 4.42 |
| `test_runs` | 0.538 | — | 14.09 | 14.86 |
| `tests_per_source_edit` | 0.386 | **0.614** | 8.16 | 6.24 |
| `test_run_ratio` | 0.402 | **0.598** | 0.2451 | 0.2192 |
| `wasted_step_ratio` | 0.520 | — | 0.0011 | 0.0046 |

读法：**成功的运行更短、改的源码更少，而且每改一次测得更勤。** 单看区分度，
最强的是运行长度（`total_steps` 0.694），但它是症状不是病因；真正有行动价值的是
`source_edits` 与验证强度。而 `wasted_step_ratio` 依然与抛硬币无异。

「测试次数」的口径包含 `python -c` 临时探针——按裁定，agent 现场写代码跑一遍也算
自我检查。若不计这些探针，每改一次测几次的区分度会更高一些，但那不是这里采用的口径。

---

## 数据与适配器

`data/raw/openhands_sample.jsonl` 是从 67,074 条池子中采样的 300 条轨迹
（150 resolved / 150 unresolved，3 个重复 `instance_id` 予以保留而非静默去重）。

写规则之前值得知道的语料事实：

- `exit_status` 为 `submit` 的有 263 条，为
  `RuntimeError: Agent reached maximum iteration (100)` 的有 37 条。
- 平均每条轨迹编辑 **11.04** 次，但平均*源码*编辑只有 **3.70** 次——
  **全部编辑中 67% 落在 scratch / test 文件上**。任何不做事先过滤的「编辑次数」规则
  都会被噪声淹没。
- 工具调用分布：`execute_bash` 9865、`str_replace_editor` 8343、`think` 801、
  `task_tracker` 264、`finish` 263。
- 本数据中*不成立*的假设：没有任何 assistant 消息会一次发起多个工具调用，
  `model_patch` 也从未为空。

适配器：`openhands`（按 `tool_call_id` 把 `tool_call` 与其 `tool` 结果配对）与
`sweagent`（把 `edit` 目标解析到 `state["open_file"]`）。两者的报错分类都只在
step 类型真的可能携带错误时才执行，因此错误指纹绝不会从文件内容里被误抓出来。

### 适配器对着真实日志验证过

`sweagent` 适配器最初只有手写的合成夹具，也就是说「支持 SWE-agent」只建立在作者对
格式的**阅读**之上。把上游仓库真实发布的日志灌进去之后，立刻塌了一个：

- 上游 22 个真实 `.traj` 中，有 1 个**完全无法识别**——那是 SWE-agent 的
  **函数调用序列化**（`history` 角色流 + 结构化 `tool_calls`），而不是 `trajectory`
  步骤列表。适配器当时只认后者。
- 现已支持两种序列化。重新验证：**21/21 全部解析成功，221 个步骤，0 个无法归类**；
  两个真实日志已作为夹具入库，`tests/test_real_trajectories.py` 每次跑测试都会重新校验。

```bash
python scripts/fetch_sweagent_trajs.py --validate   # 拉取真实日志并校验
python scripts/cross_framework.py                   # 两个框架并排统计
```

**关于跨框架对比的诚实说明：** 上面两个语料**不可比**。OpenHands 侧是 300 条
SWE-bench 任务轨迹，SWE-agent 侧只有 21 条上游演示/冒烟日志（外加 1 条真实 SWE-bench
记录）。因此 `cross_framework.py` 每次都会把这段警告打印出来——它是**适配器验证**工具，
不是给框架排名的基准。当前唯一站得住的结论是：同一条流水线能吃下两种真实日志，
且步骤词表在真实数据上没有丢失。

---

## 仓库结构

```
trajdx/
├── trajdx/                      # 核心代码包：一条日志走完的完整流水线
│   ├── __init__.py              # 包入口：暴露 AgentStep / Trajectory / detect_all
│   ├── schema.py                # 地基：AgentStep、Trajectory、三种身份键、patch_files()
│   ├── fingerprints.py          # 24 类错误指纹、exit code 提取、快速预筛
│   ├── heuristics.py            # 源码/scratch 判定、test/setup 命令识别
│   ├── metrics.py               # WSR 归因、聚合、分类区分度、过程形状指标
│   ├── report.py                # rich 终端渲染
│   ├── gold.py                  # gold patch 旁挂文件：让 localization_failure 可算
│   ├── cli.py                   # replay / diagnose / export / findings / detectors / adapters
│   │
│   ├── adapters/                # 转换层：异构日志 → 统一事件序列
│   │   ├── __init__.py          # 导入即注册全部内置适配器
│   │   ├── base.py              # 适配器接口 + 日志格式自动嗅探
│   │   ├── openhands.py         # 按 tool_call_id 把 tool_call 与结果配对
│   │   └── sweagent.py          # 解析 .traj 动作语言，靠 state["open_file"] 定位 edit
│   │
│   └── detectors/               # 规则层：纯 Python，无模型、无网络
│       ├── __init__.py          # 导入即注册全部 8 条规则
│       ├── base.py              # Finding、Category/Phase/Severity/Tier、注册表
│       ├── execution_loop.py    # 重复动作、重复报错、A-B-A-B 抖动
│       ├── localization.py      # blind_search、redundant_read、localization_failure
│       ├── verification.py      # verification_gap、weak_verification
│       ├── termination.py       # 未提交、迭代上限、patch 忽略源码
│       └── environment.py       # 反复失败的安装/环境命令、超时墙
│
├── tests/                       # 测试套件（含 README 数字的防漂移检查）
│   ├── conftest.py              # 合成轨迹与公共夹具
│   ├── test_schema.py           # 身份键、patch 解析
│   ├── test_adapters.py         # 两个框架的转换正确性
│   ├── test_detectors.py        # 规则逻辑（最大的测试文件）
│   ├── test_fingerprints.py     # 错误分类
│   ├── test_heuristics.py       # 源码/测试文件判定
│   ├── test_metrics.py          # 过程形状指标的定义
│   ├── test_gold.py             # gold 注入与 localization_failure
│   ├── test_readme_tables.py    # 说明书数字必须等于脚本输出（防漂移）
│   ├── test_real_trajectories.py # 用上游真实 .traj 日志验证适配器
│   └── data/sweagent/           # 真实的 SWE-agent 日志夹具（上游原样，MIT）
│
├── scripts/                     # 离线分析脚本，不属于运行时依赖
│   ├── fetch_trajectories.py    # 从 HF 池子下载并采样原始轨迹
│   ├── fetch_gold.py            # 拉取 gold patch 的文件集合（支持镜像，可断点续传）
│   ├── fetch_sweagent_trajs.py  # 拉取真实 SWE-agent .traj 日志，用于跨框架验证
│   ├── cross_framework.py       # 两个框架并排统计（并打印样本不可比的警告）
│   ├── detector_profile.py      # 各规则命中量与步骤覆盖率
│   ├── discrimination.py        # 各指标对 resolved 标签的 AUC
│   ├── evaluate.py              # 精确率、置信度阈值曲线、README 表格
│   └── check_regression.py      # core 规则跌破 88% 就以非零码退出
│
├── data/
│   ├── raw/                     # ⚠️ 已 gitignore：原始轨迹（约 81 MB / 300 条），
│   │                            #     需跑 fetch_trajectories.py 重新生成
│   ├── labels/                  # ✅ 入库：LLM 预标注原文 + 人工复核结论
│   ├── gold/                    # ⚠️ 已 gitignore：gold patch 的文件集合（含补集清单）
│   └── reports/                 # ⚠️ 已 gitignore：统计汇总与 stats.csv
│
├── docs/
│   └── annotation_guide.md      # 三轮标注共用的标注手册
│
├── README.md                    # 中文说明
├── README.en.md                 # 英文说明
├── pyproject.toml               # 依赖声明与 `trajdx` 命令入口
└── LICENSE                      # MIT
```

数据目录的取舍是有意的：`data/raw/` 与 `data/reports/` 体积大且可由脚本复现，
因此不进版本库；而 `data/labels/` 是**不可复现的人工结论**，必须入库。
复跑 `scripts/evaluate.py` 只需要 `data/labels/` 下的两个文件即可，不依赖原始轨迹。

## 复跑评估

```bash
# 完整评估：逐检测器精确率、置信度分档、阈值曲线
python scripts/evaluate.py \
  --findings data/labels/to_label_v3.jsonl \
  --labels "data/labels/labelled_v3_*.jsonl"

# 只要 README 用的那张表
python scripts/evaluate.py \
  --findings data/labels/to_label_v3.jsonl \
  --labels "data/labels/labelled_v3_*.jsonl" --markdown

# 门槛检查：任何一个 core 规则跌破 88%，或没有可验证样本，就以非零码退出
python scripts/check_regression.py

# gold 通道：localization_failure 的区分度（需先跑 fetch_gold.py）
python -m trajdx.cli diagnose data/raw/openhands_sample.jsonl \
  --gold data/gold/swe-rebench-gold.jsonl
```

标注文件用 `utf-8-sig` 读取，因为标注环节会写出 BOM。

最后一条依赖 `data/raw/` 与 `data/gold/` 这两个 gitignore 目录，所以在裸克隆里跑不了；
`tests/test_gold.py` 里对应的那条检查在没有数据时自动跳过，不会假装通过。

`scripts/check_regression.py` 会被测试套件一并调用，所以 core 层级不是一句承诺，
而是一条会让构建失败的断言：**规则跌破门槛时必须改代码或降级，不能只在文档里改数字。**