# trajdx

**给 Code Agent 的失败轨迹做"验尸"：告诉你它卡死在哪一步、为什么。**

语言：[中文](README.md) · [English](README.en.md)

## 一句话说明白

SWE-bench 这类评测，agent 跑完一个任务只有两个结果：过了 / 没过。没过的时候，没人告诉你它是从第几步开始跑偏的。`trajdx` 补的就是这一块：读入原始 agent 日志，用纯 Python 规则找出「在第 34~50 步反复执行同一条命令」「从头到尾没验证过自己的修改」这类具体问题，并标出对应的步骤区间。

- 检测链路里**没有大模型**，不联网，不需要 Docker
- 单条轨迹检测约 3 ms
- 支持 OpenHands 和 SWE-agent 两种日志格式

当前状态：**全部测试通过（293 个），流水线可用；9 条规则里 `termination_anomaly`（core）与 4 条 experimental 规则在本轮复核中零误报，`verification_gap` 72.7%，`blind_search` / `weak_verification` / `environment_stuck` 在当前语料上零命中（详见下文）。单条 experimental finding 别当结论读。**

## 快速上手

```bash
pip install -e ".[dev]"
pytest -q                                   # 293 个测试

python -m trajdx.cli detectors              # 有哪些规则
python -m trajdx.cli adapters               # 支持哪些日志格式
python -m trajdx.cli replay data/raw/openhands_sample.jsonl --index 0
python -m trajdx.cli diagnose data/raw/openhands_sample.jsonl
python -m trajdx.cli export data/raw/openhands_sample.jsonl --out data/reports/diag.jsonl
python -m trajdx.cli findings data/raw/openhands_sample.jsonl --out data/labels/to_label.jsonl
```

> `data/raw/` 不入库（约 81 MB），裸克隆里没有；先跑 `python scripts/fetch_trajectories.py` 重新生成，否则涉及原始轨迹的命令和部分测试会被跳过。

## 工作原理

| 阶段 | 模块 | 产出 |
|---|---|---|
| 归一化 | `trajdx.adapters` | 由 `AgentStep` 组成的 `Trajectory` |
| 报错指纹 | `trajdx.fingerprints` | 24 类错误 + exit code |
| 规则检测 | `trajdx.detectors` | 带步骤区间和证据的 `Finding` |
| 浪费量化 | `trajdx.metrics` | Wasted Step Ratio 与分类归因 |
| 渲染输出 | `trajdx.report`、`trajdx.cli` | replay / diagnose / export / findings |

一条轨迹被切成一个个 step，每个 step 是一组「决策–动作–观测」。每个 step 带三个身份键：

- `action_key` —— agent 的**意图**。路径、id、行号这些易变内容被抹平，所以 `pytest tests/test_x.py` 和 `pytest tests/test_y.py` 算「同一个动作」。
- `exact_key` —— **字面动作**，含完整输入。两次编辑同一文件，只有替换文本也一致才算「同一个动作」。
- `observation_key` —— **结果**的哈希。

为什么要分这么细？因为**重复一个动作不一定失败**：改了输入之后再重复，通常是在正常调试。循环检测靠 `action_key` 和 `exact_key` 之间的差来区分这两种情况——这是规则不冤枉人的关键。

## 规则，哪些可信？

| 规则 | 检测什么 | 可信度现状（v3 轮复核） |
|---|---|---|
| `termination_anomaly` | 结束异常：没提交、撞迭代上限、patch 忽略源码 | **core，38/38 全对**，也是区分成功/失败最有效的信号 |
| `edit_error` | 连续被编辑工具拒绝（agent 与编辑器搏斗） | **6/6 全对**（新增规则） |
| `redundant_read` | 反复读同样内容 | **4/4 全对** |
| `execution_loop` | 重复动作、重复报错、丢弃-重贴循环 | **3/3 全对**（`exact` 模式经收紧后极少触发） |
| `verification_gap` | 最终修改后没验证 | 8/11（72.7%） |
| `localization_failure` | 改错了文件（与 gold patch 对照） | 有外部真值背书（区分度第二高），无法用标注验证，停在 experimental |
| `blind_search` | 盲目搜索 | 加入收敛判据后当前语料零命中——不冤枉人，但也还没积累样本 |
| `weak_verification` | 编辑多而验证少 | 改成会话计数后当前语料零命中，同上 |
| `environment_stuck` | 反复失败的环境/安装命令 | **本语料休眠**：4,096 条真实运行里只触发 2 次（0.05%），预装容器里这个失败模式基本不存在 |

`replay` / `findings` 默认不输出 experimental 规则；要拿全量做聚合分析用 `--tier all`。

下面三张表是上表「可信度现状」的依据，全部可复现。

### 表 1：历史标注精确率（旧标签 `--allow-legacy` 对照）

这张表由下面的命令直接生成，不是手写的；测试会校验两份 README 与脚本输出逐字一致。

```bash
python scripts/evaluate.py --labels "data/labels/labelled_v3_*.jsonl" --markdown --allow-legacy
```

| Detector | Tier | Precision | n |
|---|---|---|---|
| `execution_loop` | experimental | 100.0% | 1 |
| `termination_anomaly` | core | 95.5% | 22 |
| `verification_gap` | experimental | 0.0% | 1 |
| `blind_search` | experimental | — | 0 |
| `edit_error` | experimental | — | 0 |
| `environment_stuck` | experimental | — | 0 |
| `localization_failure` | experimental | — | 0 |
| `redundant_read` | experimental | — | 0 |
| `weak_verification` | experimental | — | 0 |
| **整体** | | **91.7%** | **24** |

读法只有一条要紧的：**先看 n 再看精确率**。`n=0`（显示为「—」）表示这条规则在该轮没有命中样本，不是 100% 准，两者绝不能混读。仓库内可复现的人工复核结论共 **145 条**（v2 + v3 两轮，204 行去掉 59 条重复，覆盖 88 条轨迹），更早与更晚的轮次没有随仓库发布。

这是标注闭环跑出来的**诚实结果，不是一个被达成的目标**：命中假设的规则被保留并提层，其余标为「未证实」，而不是悄悄调到数字好看为止。

### 表 2：当前代码 62 条 finding 的独立 AI 复核（不是人工）

`data/labels/reviewed_ai_identity_v3.jsonl` 是对当前代码全部 62 条 finding 的独立 AI 上下文审查。复核包屏蔽了任务结局（resolved 与否），不复读、不沿用旧判决，每条都带 run/证据签名与代码、原始数据哈希。**它是 AI 复核不是人工标注，也不是独立留出集：每条都标着 `reviewer_type=ai`、`human_verified=false`。**

| 检测器 | valid | invalid | uncertain | 精确率 |
|---|---:|---:|---:|---:|
| `termination_anomaly` | 38 | 0 | 0 | 100% |
| `edit_error` | 6 | 0 | 0 | 100% |
| `redundant_read` | 4 | 0 | 0 | 100% |
| `execution_loop` | 3 | 0 | 0 | 100% |
| `verification_gap` | 8 | 3 | 0 | 72.7% |
| **整体** | **59** | **3** | **0** | **95.2%** |

严格门禁（n≥20、core 覆盖率≥80%、点估计≥88%）按这批复核标签**通过**：`termination_anomaly` 38 条、100%、覆盖 100%。

上一轮（v2）发现的 5 类已知误报——复现脚本不算验证、`checker.py`/`checkpoint.py` 被 scratch 正则误伤、`C-c` 被当成重复命令、编辑计数被反复改写灌水、收敛式探索被判盲搜——**已在本轮全部修复**：v2 轮 66 条判决中 21 条失效的 finding 不会再以同型证据出现。本轮仅剩的 3 个误报同型：`verification_gap` 把「创建验证脚本」误计为源码编辑（如 `edge_case_tests.py` 不在 scratch 命名正则内），这是下一个要修的病灶。新增的 `revert_cycle` 子模式曾把 `git stash pop`（恢复工作）误判为丢弃，已修复并有回归测试；当前它没有命中样本。

**修完规则必须重新复核，不能沿用判决——本轮就是这么做的。**

### 表 3：gold 通道——`localization_failure` 的区分度

`localization_failure` 不走标注通道：它直接把 agent 的 patch 与标准答案（gold patch）改动的文件集合做比较。轨迹日志里没有 gold patch，必须从任务数据集（SWE-rebench）拉取：

```bash
python scripts/fetch_gold.py            # 取 gold 文件集合，写成旁挂文件
python -m trajdx.cli diagnose data/raw/openhands_sample.jsonl \
  --gold data/gold/swe-rebench-gold.jsonl
```

`huggingface.co` 连不上（超时、SSL 中断、分片卡 0 字节）就加 `--mirror https://hf-mirror.com`；脚本按条落盘，中断后重跑会自动跳过已取到的。诊断时会打印覆盖率，因为「旁挂文件没匹配上」和「规则确实没发现问题」在输出里长得一模一样。`--gold` 对 `replay` / `diagnose` / `export` / `findings` 都可用。

gold 通道接通后的实测（297/297 个 `instance_id` 全部取到，覆盖 300/300 条轨迹，覆盖率 **100%**）：

| | resolved | unresolved | Δ |
|---|---|---|---|
| `termination_anomaly` | 5.3%（8/150） | 20.7%（31/150） | +15.3 |
| `localization_failure` | 3.3%（5/150） | **18.0%（27/150）** | **+14.7** |

按轨迹命中率计（一条轨迹命中多条同类 finding 只算一次）。`localization_failure` 的区分度全表第二，仅次于 `termination_anomaly`，也是被标出的浪费步数的主要来源——`diagnose` 报告的平均 1.44 步浪费里，1.36 步来自它。它的层级仍然停在 experimental：88% 那条线是按「标注精确率」定义的，这条规则没法用标注验证（标注里没有 gold），换一把尺子把它提进 core 会让 core 失去含义。它是目前唯一一条有外部真值背书的规则。

## Wasted Step Ratio：一个没达到预期的指标

`WSR` 按严重度把每个被标记的步骤恰好归入一个浪费分类，恒有 `WSR <= 1.0`，没有任何步骤被重复计费。

```bash
python scripts/detector_profile.py     # 各规则命中量与步骤覆盖率
python scripts/discrimination.py       # 各指标对 resolved 标签的 AUC
```

**直说：WSR 不能预测失败。** 对 resolved/unresolved 标签的 Mann-Whitney AUC 只有 **0.520**——和抛硬币无异。把它当失败预测器是错的，所以文档和 CLI 严格把它描述成**效率指标**：它回答「这次运行有多少比例在原地打转」，不回答「这次运行会不会成功」。

真正携带信号的是「过程形状」类指标。下表由 `scripts/discrimination.py` 直接输出（AUC 的正类是*失败*，所以小于 0.5 表示「数值越低越容易失败」，反向后就大于 0.5）：

| 指标 | AUC（正类=失败） | 反向 | Resolved | Unresolved |
|---|---|---|---|---|
| `total_steps` | 0.694 | — | 58.83 | 71.41 |
| `source_edits` | 0.637 | — | 2.99 | 4.51 |
| `test_runs` | 0.538 | — | 14.09 | 14.86 |
| `tests_per_source_edit` | 0.382 | **0.618** | 8.16 | 6.17 |
| `test_run_ratio` | 0.402 | **0.598** | 0.2451 | 0.2192 |
| `novel_observation_ratio` | 0.430 | **0.570** | 0.9269 | 0.9174 |
| `wasted_step_ratio` | 0.520 | — | 0.0008 | 0.0017 |

一句话读法：**成功的运行更短、改的源码更少、每改一次测得更勤。** 区分度最强的是运行长度（`total_steps` 0.694），但那是症状不是病因；真正有行动价值的是 `source_edits` 与验证强度。

`novel_observation_ratio`（新信息率）是本轮新增的指标：一条轨迹里产出「从未见过的观测」的步骤占比，直接从 `observation_key` 哈希算出，**不经过任何规则**。它比 WSR（0.520）强，但仍弱于验证强度类指标——诚实记录：观测新颖度有信号，但不是决定性的。

口径说明：「测试次数」包含 `python -c` 现场探针——agent 现写代码跑一遍也算自我检查。不计这些探针的话，tests-per-edit 的区分度会更高一些，但那不是这里采用的口径。

## 数据与适配器

`data/raw/openhands_sample.jsonl` 是从 67,074 条池子中采样的 300 条轨迹（150 resolved / 150 unresolved，3 个重复 `instance_id` 予以保留而非静默去重）。

写规则之前值得知道的语料事实：

- `exit_status` 为 `submit` 的有 263 条，为 `RuntimeError: Agent reached maximum iteration (100)` 的有 37 条。
- 平均每条轨迹编辑 **11.04** 次，但平均*源码*编辑只有 **3.70** 次——**全部编辑中 67% 落在 scratch / test 文件上**。任何不做事先过滤的「编辑次数」规则都会被噪声淹没。
- 工具调用分布：`execute_bash` 9865、`str_replace_editor` 8343、`think` 801、`task_tracker` 264、`finish` 263。
- 本数据中*不成立*的假设：没有任何 assistant 消息会一次发起多个工具调用，`model_patch` 也从未为空。

适配器：`openhands`（按 `tool_call_id` 把 `tool_call` 与其 `tool` 结果配对）与 `sweagent`（把 `edit` 目标解析到 `state["open_file"]`）。两者只在 step 类型真的可能携带错误时才做错误分类，所以错误指纹绝不会从文件内容里被误抓出来。

### 适配器对着真实日志验证过

`sweagent` 适配器最初只有手写的合成夹具，「支持 SWE-agent」只建立在作者对格式的**阅读**之上。灌入上游真实发布的日志后立刻塌了一个：22 个真实 `.traj` 里有 1 个**完全无法识别**——那是 SWE-agent 的函数调用序列化（`history` 角色流 + 结构化 `tool_calls`），而不是适配器唯一认识的 `trajectory` 步骤列表。

现已两种序列化都支持。重新验证：**21/21 全部解析成功，221 个步骤，0 个无法归类**；两个真实日志已作为夹具入库，`tests/test_real_trajectories.py` 每次跑测试都会重新校验。

```bash
python scripts/fetch_sweagent_trajs.py --validate   # 拉取真实日志并校验
python scripts/cross_framework.py                   # 两个框架并排统计
```

**跨框架对比的老实说明：** 两个语料**不可比**——OpenHands 侧是 300 条 SWE-bench 任务轨迹，SWE-agent 侧只有 21 条上游演示/冒烟日志（外加 1 条真实 SWE-bench 记录）。`cross_framework.py` 每次都会把这段警告打印出来：它是**适配器验证**工具，不是给框架排名的基准。当前唯一站得住的结论：同一条流水线能吃下两种真实日志，且步骤词表在真实数据上没有丢失。

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
│       ├── __init__.py          # 导入即注册全部 9 条规则
│       ├── base.py              # Finding、Category/Phase/Severity/Tier、注册表
│       ├── execution_loop.py    # 重复动作、重复报错、A-B-A-B 抖动、丢弃-重贴循环
│       ├── edit_error.py        # 连续被编辑工具拒绝
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
│   ├── test_identity_loading.py # iter_file / limit 与版本化标签的加载
│   ├── test_pool_validation.py  # 有界分层抽样验证
│   ├── test_reviewed_labels.py  # AI 复核标签的一致性检查
│   ├── test_readme_tables.py    # 说明书数字必须等于脚本输出（防漂移）
│   ├── test_real_trajectories.py # 用上游真实 .traj 日志验证适配器
│   └── data/sweagent/           # 真实的 SWE-agent 日志夹具（上游原样，MIT）
│
├── scripts/                     # 离线分析脚本，不属于运行时依赖
│   ├── fetch_trajectories.py    # 下载并采样 300 条原始轨迹
│   ├── fetch_openhands_pool.py  # 从发布的 SWE-rebench 数据集按需拉取 67k 条轨迹池
│   ├── fetch_sweagent_pool.py   # 拉取 80k 条 SWE-agent 轨迹池并转成 JSONL
│   ├── fetch_gold.py            # 拉取 gold patch 的文件集合（支持镜像，可断点续传）
│   ├── fetch_sweagent_trajs.py  # 拉取真实 SWE-agent .traj 日志，用于跨框架验证
│   ├── cross_framework.py       # 两个框架并排统计（并打印样本不可比的警告）
│   ├── detector_profile.py      # 各规则命中量与步骤覆盖率
│   ├── discrimination.py        # 各指标对 resolved 标签的 AUC
│   ├── evaluate.py              # 精确率、置信度阈值曲线、README 表格
│   ├── check_regression.py      # core 规则跌破 88% 就以非零码退出
│   ├── validate_pool.py         # 有界分层解析验证（无网络、不做精确率声明）
│   ├── prepare_review.py        # 生成屏蔽结局的独立复核证据包
│   ├── record_termination_review.py  # 落盘 termination 相关复核判决
│   ├── record_verification_review.py # 落盘 verification 相关复核判决
│   └── finalize_review.py       # 汇总复核判决，校验覆盖并写成版本化标签
│
├── data/
│   ├── raw/                     # ⚠️ 已 gitignore：原始轨迹（约 81 MB / 300 条），
│   │                            #     需跑 fetch_trajectories.py 重新生成
│   ├── labels/                  # ✅ 入库：LLM 预标注原文 + 人工复核结论 + AI 复核判决
│   ├── gold/                    # ⚠️ 已 gitignore：gold patch 的文件集合（含补集清单）
│   └── reports/                 # ⚠️ 已 gitignore：统计汇总与 stats.csv
│
├── docs/
│   ├── annotation_guide.md      # 三轮标注共用的标注手册
│   └── bugfix_validation.md     # 关键正确性修复与有界数据验证的记录
│
├── README.md                    # 中文说明
├── README.en.md                 # 英文说明
├── pyproject.toml               # 依赖声明与 `trajdx` 命令入口
└── LICENSE                      # MIT
```

数据目录的取舍是有意的：`data/raw/`、`data/gold/`、`data/reports/` 体积大且可由脚本复现，不进版本库；`data/labels/` 是**不可复现的复核结论**，必须入库。复跑评估必须有原始轨迹。v2/v3 旧标签仅可用 `--allow-legacy` 做历史对照；当前评估使用 `data/labels/reviewed_ai_identity_v3.jsonl`（独立 AI 复核）。

## 复跑评估

```bash
# 当前复核标签：严格门禁 + 完整评估
# evaluate.py 每次都会重跑当前检测器，存储标签只用来对账，绝不直接计分
python scripts/check_regression.py --labels data/labels/reviewed_ai_identity_v3.jsonl
python scripts/evaluate.py --labels data/labels/reviewed_ai_identity_v3.jsonl

# 历史对照（不是当前质量证明）
python scripts/evaluate.py --labels "data/labels/labelled_v3_*.jsonl" --allow-legacy

# README 引用的那张历史对照表
python scripts/evaluate.py \
  --labels "data/labels/labelled_v3_*.jsonl" --markdown --allow-legacy

# gold 通道：localization_failure 的区分度（需先跑 fetch_gold.py）
python -m trajdx.cli diagnose data/raw/openhands_sample.jsonl \
  --gold data/gold/swe-rebench-gold.jsonl

# 有界解析验证：只扫前 1200 条、分层抽样、逐条诊断
python scripts/validate_pool.py --input data/raw/openhands_pool.jsonl \
  --sample-out data/raw/validation_openhands.jsonl \
  --report data/reports/validation_openhands_after.json
```

标注文件用 `utf-8-sig` 读取，因为标注环节会写出 BOM。

gold 那条依赖 gitignore 的 `data/raw/` 与 `data/gold/`，所以裸克隆里跑不了；`tests/test_gold.py` 对应的检查在没数据时自动跳过，不假装通过。

严格门禁对 `reviewed_ai_identity_v3.jsonl` 当前是**通过**的；测试套件同时会校验它拒绝无身份的旧标签。**verdict 永远不能自动迁移来制造通过：证据变了（规则改了、finding 换了身份）就必须重新复核。** 新判决必须保留 `finding_id`、`run_id`、`finding_signature`。

JSONL 加载 API 支持 `iter_file` 与读取前生效的 `limit`；JSON 数组文件仍需整份解析。`validate_pool.py` 的样本不代表全语料，不产生 precision/recall，也不能给框架排名。
