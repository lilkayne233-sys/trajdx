# trajdx

**给 Code Agent 的失败轨迹做"验尸"：告诉你它卡死在哪一步、为什么。**

SWE-bench 这类评测，agent 跑完一个任务只有两个结果：过了 / 没过。没过的时候，没人告诉你它是从第几步开始跑偏的。`trajdx` 补的就是这一块：读入原始 agent 日志，用纯 Python 规则找出「在第 34~50 步反复执行同一条命令」「从头到尾没验证过自己的修改」这类具体问题，并标出对应的步骤区间。

- 检测链路里**没有大模型**，不联网，不需要 Docker
- 单条轨迹检测约 3 ms
- 支持 OpenHands 和 SWE-agent 两种日志格式
- 当前可信度：1,400 条真实 OpenHands 轨迹上逐条复核了 202 个报警，**96.0% 是真问题**

`diagnose` 的输出长这样（示意，实际是 rich 彩色终端渲染）：

```
┌ astropy__astropy-12990  ·  openhands  ·  UNRESOLVED ┐
│ 78 steps   wasted 12 (15.4%)   3 finding(s)          │
│ 4 source edit(s) · 9 test run(s)                     │
└──────────────────────────────────────────────────────┘

  ● MEDIUM verification_gap    verify   steps 51-78  ·  4 wasted
      最后一次源码编辑之后没有任何测试运行
  ● HIGH   edit_error          execute  steps 30-38  ·  9 wasted
      同一文件连续 3 次被编辑工具拒绝，报错均为语法错误
```

## 快速上手

```bash
pip install -e ".[dev]"
pytest -q                                   # 261 个测试

python -m trajdx.cli detectors              # 有哪些规则
python -m trajdx.cli adapters               # 支持哪些日志格式
python -m trajdx.cli replay data/raw/openhands_sample.jsonl --index 0
python -m trajdx.cli diagnose data/raw/openhands_sample.jsonl
python -m trajdx.cli export data/raw/openhands_sample.jsonl --out data/reports/diag.jsonl
```

> `data/raw/` 不入库（约 81 MB），裸克隆里没有；先跑 `python scripts/fetch_trajectories.py` 重新生成，否则涉及原始轨迹的命令和部分测试会被跳过。

## 它能查出什么

4 条规则，只保留验证得住的。前 3 条靠日志里的硬事实（patch、工具报错、测试记录），第 4 条靠标准答案对照。

| 规则 | 大白话 |
|---|---|
| `termination_anomaly` | 结束异常：没提交、撞了迭代上限、patch 里没改源码 |
| `edit_error` | agent 连续被编辑工具拒绝，在和编辑器搏斗 |
| `verification_gap` | 改完代码后没验证（或最后一次编辑之后没再验证） |
| `localization_failure` | 改错了文件（和标准答案对照） |

精确率的完整依据在下面「数字有多可信」。`replay` / `findings` 默认只输出可信的规则，全量分析加 `--tier all`。

## 工作原理

一条轨迹被切成一个个 step，每个 step 是一组「决策–动作–观测」。规则不看自然语言，只看每个 step 的三个身份键：

- `action_key` —— agent 的**意图**。路径、行号这些易变内容被抹平，所以 `pytest tests/test_x.py` 和 `pytest tests/test_y.py` 算「同一个动作」。
- `exact_key` —— **字面动作**，含完整输入。两次编辑只有替换文本也一致才算同一个。
- `observation_key` —— **结果**的哈希。

为什么要分这么细？因为**重复一个动作不一定失败**：改了输入之后再重复，通常是在正常调试。重复检测靠 `action_key` 和 `exact_key` 之间的差来区分这两种情况——这是规则不冤枉人的关键。

## 数字有多可信

报警会拿去让独立的 AI 复核（屏蔽任务结局，逐条判 valid / invalid）。两张表由脚本直接生成，测试会校验 README 与脚本输出一致。**先看 n 再看精确率**：n=0（显示「—」）表示该轮没有命中样本，不是 100% 准。

**主样本**：1,400 条 OpenHands 轨迹，202 条 finding，整体精确率 **96.0%**。

```bash
python scripts/evaluate.py --raw data/raw/openhands_sample_v6.jsonl \
  --labels data/labels/reviewed_ai_identity_v6.jsonl --markdown
```

| Detector | Tier | Precision | n |
|---|---|---|---|
| `edit_error` | experimental | 100.0% | 21 |
| `verification_gap` | experimental | 96.2% | 26 |
| `termination_anomaly` | core | 95.5% | 155 |
| `localization_failure` | experimental | — | 0 |
| **整体** | | **96.0%** | **202** |

**跨框架对照**：188 条 SWE-agent（llama-8B 弱模型）轨迹，227 条 finding，整体 83.3%。排序不变（`edit_error` 最稳），但弱模型会引入新的误报形态，experimental 层的数字必须绑定语料读。

| Detector | Tier | Precision | n |
|---|---|---|---|
| `edit_error` | experimental | 100.0% | 59 |
| `verification_gap` | experimental | 77.6% | 67 |
| `termination_anomaly` | core | 77.2% | 101 |
| `localization_failure` | experimental | — | 0 |
| **overall** | | **83.3%** | **227** |

`localization_failure` 另有一条不走复核的验证通道：把 agent 改动的文件和标准答案（gold patch）对照。在 300 条轨迹上，失败轨迹的命中率 18.0%，成功轨迹 3.3%——区分度全项目第二，仅次于 `termination_anomaly`。

## 一个没达到预期的指标

`WSR`（浪费步数占比）本来想用来预测成败，实测 AUC 只有 **0.514**——和抛硬币无异。所以它只作为效率指标使用。真正有信号的是这些过程指标（AUC 越偏离 0.5 越好）：

| 指标 | AUC（正类=失败） | 反向 |
|---|---|---|
| `total_steps` | 0.664 | — |
| `source_edits` | 0.618 | — |
| `tests_per_source_edit` | 0.399 | **0.601** |
| `test_run_ratio` | 0.426 | **0.574** |
| `wasted_step_ratio` | 0.514 | — |

一句话读法：**成功的运行更短、改的源码更少、每改一次测得更勤。**（有趣的反转：在 8B 弱模型语料上 WSR 的 AUC 跳到 0.687——强模型空转也产出新信息，弱模型的空转是真·原地打转。）

## 复现

```bash
python scripts/check_regression.py --raw data/raw/openhands_sample_v6.jsonl \
  --labels data/labels/reviewed_ai_identity_v6.jsonl     # core 规则跌破 88% 就报错退出
python scripts/evaluate.py --raw data/raw/openhands_sample_v6.jsonl \
  --labels data/labels/reviewed_ai_identity_v6.jsonl --markdown
python -m trajdx.cli diagnose data/raw/openhands_sample.jsonl \
  --gold data/gold/swe-rebench-gold.jsonl                # gold 通道，需先跑 fetch_gold.py
```

`evaluate.py` 每次都会重跑当前检测器，存储的复核标签只用来对账，绝不直接计分。规则改动之后必须重新复核，旧的判决不能沿用。更多评估口径与标注手册见 [docs/](docs/)。

## 仓库结构

```
trajdx/            # 核心包：adapters（两种日志 → 统一步骤）→ fingerprints（24 类错误）
                   #   → detectors（4 条规则）→ metrics（WSR 与过程指标）→ cli / report
tests/             # 261 个测试，含「README 数字必须等于脚本输出」的防漂移检查
scripts/           # 离线工具：拉取轨迹池、抽样、评估、复核闭环、回归门禁
data/raw|gold/     # gitignore，脚本可重建；data/labels/ 是复核结论，入库
docs/              # 标注手册、关键修复的验证记录
```
