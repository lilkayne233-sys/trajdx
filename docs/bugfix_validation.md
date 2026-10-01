# 关键正确性修复与有界数据验证

## 范围

本轮不下载数据、不全量加载两个数据池、不改人工 verdict、不提交 Git。

修复包括 CRLF 正文丢失、路径中间目录误删、工具退出码优先级、error_kind-only 失败状态、编辑身份范围/真实路径、混合 diff 文件遗漏、重复编辑后验证时序、exact/error 重复 finding，以及按任务起点构造 ID 的身份缺陷。

新增 JSONL `iter_file` 与读取前生效的 `limit`，以及有界分层抽样脚本。JSON 数组仍整份解析；无 limit 的列表 API 仍持有全部轨迹。

## 样本与修复前后对照

基线为 HEAD `9d1d205` 的归档代码，不含本轮修改。两版对完全相同的抽样文件逐条运行；SHA256 相同才比较。

抽样 seed=42，仅扫描每个数据池前 1200 条，按 resolved/target 与序列化消息长度分层，每层最多50条。不是完整数据池的随机样本，也未按仓库进一步分层，因此不能外推、评估精确率/召回率或排名框架。

| 指标 | OpenHands 修复前 → 后 | SWE-agent 修复前 → 后 |
|---|---:|---:|
| 样本轨迹 | 200 → 200 | 244 → 244 |
| 解析成功 | 200 → 200 | 244 → 244 |
| 空轨迹 / 解析检测异常 | 0 / 0 → 0 / 0 | 0 / 0 → 0 / 0 |
| 归一化步骤 | 12685 → 12685 | 10767 → 10767 |
| finding 数 | 28 → 32 | 1064 → 1060 |
| verification_gap | 5 → 9 | 147 → 151 |
| execution_loop | 1 → 1 | 663 → 656 |
| termination_anomaly | 22 → 22 | 143 → 142 |
| 重复 finding ID 次数 | 0 → 0 | 174 → 0 |
| 标记浪费步骤 | 11 → 11 | 4389 → 4388 |
| 仓库覆盖（缺 repo 时从 instance_id 推导） | 159 | 43 |

OpenHands 样本包含100成功/100失败；SWE-agent包含94成功/150失败。SWE-agent成功长轨迹在扫描范围内仅1条，样本不平衡已保留并披露。

重复 ID 下降主要说明不同运行/不同模式不再被旧 location ID 合并，不等于新增发现都准确。验证缺口增加符合修复最终编辑时序后的行为，但仍须标注判断合理性。

样本未观察到 error_kind-but-not-failed；CRLF/路径/退出码等反例主要由专门回归测试验证，不能声称每个修复都在本批真实数据里产生变化。

## 新的评估契约

版本化 finding ID 包含规范化运行内容和完整 finding 签名：模式、范围、证据、浪费步骤、严重度、confidence 与 detail。同一任务的不同运行不再只靠任务 ID 合并。证据或归一化变化会使旧标签失效；这是保守设计。

旧 v2/v3 标签只有 location ID，严格评估默认拒绝。`--allow-legacy` 仅为明确披露风险的历史对照，且有歧义的 location 不匹配。没有自动迁移或重新裁定 verdict。

原300条语料：当前66个唯一 finding；83条旧标签严格模式0匹配，全部需复核身份。历史模式32条唯一匹配、51条失效/歧义、34条当前 finding 无旧匹配：历史点估计81.2%（26/32），termination 95.5%（21/22）。这个数字不代表修复后精确率提高，分母和匹配契约已变。

严格回归门禁在修复版本上没有新身份人工标签时说明符号，因此新增 `data/labels/reviewed_ai_identity_v2.jsonl`：当前代码全部66条 finding 的独立 AI 复核判决。

## 独立 AI 复核（非人工）

复核为独立 AI 上下文审查，**不是人工标注，也不是独立留出集**。证据包屏蔽了 resolved 结果，未读取或沿用任何旧 verdict；每条判决保留 finding_id、run_id、finding_signature 与代码/原始数据哈希。

| 检测器 | valid | invalid | uncertain | 精确率 |
|---|---:|---:|---:|---:|
| termination_anomaly | 38 | 2 | 0 | 95.0% |
| redundant_read | 3 | 1 | 0 | 75.0% |
| verification_gap | 7 | 5 | 1 | 58.3% |
| execution_loop | 2 | 3 | 0 | 40.0% |
| blind_search | 0 | 1 | 0 | 0.0% |
| weak_verification | 0 | 3 | 0 | 0.0% |
| **合计** | **50** | **15** | **1** | **76.9%** |

严格门禁（n≥20、覆盖≥80%、点估计≥88%）现通过：`termination_anomaly` 40条、95.0%、覆盖100%。

主要误报值得直接修规则：

1. `verification_gap/stale_verification` 只看 pytest 套件，把“代理自写并运行的复现脚本”算作没验证（5条invalid中有3条属此类）。
2. `termination_anomaly/patch_ignores_source` 的 scratch 正则把 `checkpoint.py` 当 `check*`、把根目录探针当源码，2条 invalid 实际都改了库源码。
3. `execution_loop/exact` 把 `C-c` 当成重复失败命令——它每次都在中止不同进程。
4. `weak_verification` 的编辑计数被重复改写、revert-reapply、语法失败编辑放大，且不把探针运行算作验证。
5. `blind_search/read_without_edit` 把沿调用链收敛的跨层探索判为盲搜。

因此“76.9%”应由规则修复后重新复核，而不是当作修复后质量的最终结论。`termination_anomaly` 的95%只覆盖40条，仍无置信区间。

WSR AUC 当前0.516（旧0.520），仍接近随机。阈值曲线的 recall 列改称 retained，避免误当真实召回率。

## 最终回归结果

`python -m pytest -q`：**270 passed**；`git diff --check` 通过。真实 OpenHands 样本的 CLI 导出冒烟检查确认新 finding_id/run_id/finding_signature 字段可用于后续复核。

## 待办与限制

1. 按上面的误报清单修规则（复现脚本算验证、scratch 正则、C-c、编辑计数、收敛探索），修完后重新复核，不要直接沿用本轮 verdict。
2. 抽查未触发 finding 的失败轨迹，开始评估漏检；留出按任务/仓库分组的验证集与人工抽检。
3. SWE-agent样本没有显式非零退出码，并有70个空 shell/edit 观测；OpenHands有16个空观测。这是观测覆盖统计，不自动等同解析丢失；需对原始工具输出逐例审查。
4. 无已知 footer 时退出码仍保留旧正文回退；路径起点真实 app/code 与容器根歧义仍在。
5. 尚未统一 metrics 与 detector 的 source edit 口径，也未改变 empty_patch 全轨迹浪费定义；它们属于下一轮指标设计问题。
6. 最小样本/覆盖门禁不等于统计保证，且本轮复核标签并非人工标准答案；后续需要独立样本与置信区间。

## 复跑

```powershell
python -m pytest -q
python scripts/prepare_review.py            # 冻结当前 finding 与证据包
python scripts/record_termination_review.py # 40条终止异常判决
python scripts/record_verification_review.py # 13条验证缺口 + 3条弱验证判决
python -m scripts.finalize_review           # 校验66条覆盖/身份并写 reviewed_ai_identity_v2.jsonl
python scripts/check_regression.py --labels data/labels/reviewed_ai_identity_v2.jsonl
python scripts/evaluate.py --labels data/labels/reviewed_ai_identity_v2.jsonl
# 历史对照仍可用：
python scripts/evaluate.py --labels "data/labels/labelled_v3_*.jsonl" --markdown --allow-legacy
```

证据包与机器报告留在 gitignored data/reports；复核判决随源码入库。

原始样本与详细机器报告留在 gitignored data/raw 和 data/reports；本说明与脚本/测试随源码保留。
