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

规则基于 300 条真实 OpenHands 轨迹（150 resolved / 150 unresolved）构建，并经三轮
**共 350 条「大模型预标注 + 人工复核」**的 finding 校准。下表数字来自留出的 v3 轮
（能干净 join 到标注的 83 条 finding）。

| 检测器 | 层级 | Precision | n |
|---|---|---|---|
| `redundant_read` | **core** | 100% | 4 |
| `termination_anomaly` | **core** | 91.7% | 24 |
| `execution_loop` | experimental | 66.7% | 3 |
| `verification_gap` | experimental | 53.8% | 13 |
| `weak_verification` | experimental | 18.2% | 11 |
| `blind_search` | experimental | 0% | 1 |
| `environment_stuck` | experimental | — | 1 |
| `localization_failure` | experimental | — | 0 |
| **整体** | | **45.8%** | **83** |

**只有两个检测器越过了 88% 精确率这条线。** 其余规则都照常随包发布，但被标记为
`Tier.EXPERIMENTAL`，并且 `replay` / `findings` 默认不输出 experimental 规则——
单条 experimental finding 不应被当作结论来读。需要样本量做聚合分析时用 `--tier all`，
聚合表会同时披露各分类的区分度。

这是标注闭环跑出来的**诚实结果，不是一个被达成的目标**。命中假设的规则被保留并提层，
其余的被标为「未证实」，而不是悄悄调到数字好看为止。

### 单条规则的说明

- **`execution_loop`** 有一个很尖锐的失效模式：*中间夹了编辑*的循环，有效率只有
  3.4%（n=29）；*中间没有编辑*的循环有效率 66.7%（n=3）。加上「中间无编辑」的
  门槛后，全语料命中量从 148 条降到 5 条——这也是它现在很少触发的原因。
  **改动输入之后再重复动作，通常属于合法的调试行为。**
- **`localization_failure`** 把 agent 的 patch 与 gold patch 的文件集合做比较。
  缺少 `meta["gold_files"]` 时它按设计保持沉默，而不是用启发式去猜。
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

真正携带信号的是「过程形状」类指标：

| 指标 | AUC | Resolved | Unresolved |
|---|---|---|---|
| `total_steps` | 0.694 | 58.8 | 71.4 |
| `tests_per_source_edit` | 0.342 → 反向 0.658 | 0.88 | 0.66 |
| `test_run_ratio` | 0.350 → 反向 0.650 | — | — |
| `source_edits` | — | 9.6 | 12.5 |
| `wasted_step_ratio` | 0.520 | — | — |

**成功的运行「每次编辑测得更勤」。** 失败的运行编辑更多、跑得更久。整个语料里
最强的可行动信号是验证强度，而不是浪费。

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

---

## 仓库结构

```
trajdx/
  schema.py           AgentStep / Trajectory、三种身份键、patch_files()
  fingerprints.py     24 类错误指纹、exit code 提取、快速预筛
  heuristics.py       源码/scratch 判定、test/setup 命令识别
  adapters/           openhands.py、sweagent.py
  detectors/          base.py（注册表、层级），execution_loop、localization、
                      verification、termination、environment
  metrics.py          WSR 归因、聚合、分类区分度
  report.py           rich 渲染
  cli.py              replay / diagnose / export / findings / detectors / adapters
tests/                119 个测试
scripts/              fetch_trajectories、detector_profile、discrimination、evaluate
docs/annotation_guide.md    三轮标注共用的标注手册
data/labels/          大模型预标注原文 + 人工复核结论
```

## 复跑评估

```bash
python scripts/evaluate.py \
  --findings data/labels/to_label_v3.jsonl \
  --labels "data/labels/labelled_v3_*.jsonl"
```

标注文件用 `utf-8-sig` 读取，因为标注环节会写出 BOM。