"""Persist independent AI review verdicts for verification and weak-verification packets.

Decisions are enumerated per packet from evidence read directly (post-edit command
sequences, edited paths, observations). No legacy label and no task outcome is used.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

root = Path('data/reports/review_v2')
VERIFICATION = {
    1: ('invalid', '最后源码编辑85改cfdm/netcdfread.py后，86、89、93三次运行reproduce_issue.py实际导入并执行被改库代码（均以库内ValueError/脚本错误退出）。所称“之后15步未经检查”不成立：代理确实在最终状态上重跑了复现脚本，只是未重跑pytest套件。', [85,86,89,93]),
    2: ('invalid', '最后编辑49是仓库根的探针脚本edge_cases.py，50立即用 pyupgrade.py --py36-plus edge_cases.py 执行被测工具并对比输出，51查看前后差异，52提交。最终编辑非库源码，且被测工具在其后被执行，不构成“提交未验证的最终源码变更”。', [47,48,49,50,51,52]),
    3: ('invalid', '最后编辑98是探针original_repro.py，99立刻运行它；真正的库编辑95/96已在97用reproduce_issue.py验证成功（退出码0）。因此最终库源码变更是被验证过的，98只是探针文件，不能作为“最后修改未验证”的依据。', [95,96,97,98,99]),
    4: ('valid', '最后动作为99编辑sqlglot/parser.py（补充STRUCT闭合的解析逻辑），其后没有任何步骤；96的复现运行早于98、99两次源码修改。最终源码变更未再执行任何套件或探针，符合stale_verification。', [96,97,98,99]),
    5: ('valid', '最后源码编辑98改src/arta/_engine.py（新增布尔键预处理调用），之后99仅读取该文件，没有运行任何测试、套件或探针。最终变更未被验证。', [94,95,96,97,98,99]),
    6: ('valid', '最终源码编辑98删除falcon/media/multipart.py中多余的DelimiterError处理分支；其后99仅运行 python -m py_compile（语法编译检查，且观测被截断）。语法检查不能验证删除异常处理后的行为，最终语义变更未被测试。', [91,92,93,98,99]),
    7: ('valid', '最后源码编辑99改src/psyclone/psyir/frontend/fparser2.py，其后无任何步骤；87起的后续检查均为探针脚本与grep。最终库变更未再执行测试或探针。', [87,93,98,99]),
    8: ('valid', '最后编辑99改pynamodb/models.py的_from_data（哈希键名转换与去序列化路径），96的测试运行早于该修改，其后无任何执行步骤。最终变更未验证。', [96,97,98,99]),
    9: ('valid', '最后源码编辑97改dvc/state.py的mtime_and_size（为普通文件直接取size），之后98、99只有plan步骤，没有任何执行。最终变更未验证。', [94,97,98,99]),
    10: ('invalid', '最后编辑55是/workspace根下的演示脚本before_after_comparison.py（非库源码），56立即运行它；库文件describe.py的改动在此之前已由50、52两次运行探测验证。检测器把演示脚本误当作源码编辑。', [49,50,52,55,56,57]),
    11: ('invalid', '最后编辑97是仓库根调试探针understand_execution_flow.py，98立即运行它；真正库改动在此之前，最终“编辑”是调试脚本且被执行，不构成未验证的源码变更。', [94,97,98]),
    12: ('uncertain', '最后源码编辑98改sqlglot/parser.py后，99确实启动了 python reproduce_issue.py 的验证运行，但该步观测为空（运行在100步上限处被截断），无法判断这次运行是成功还是失败。既不能确认最终变更已被验证，也不能确认完全未检查。', [97,98,99]),
    13: ('valid', '最后编辑99改sqlglot/parser.py的FACTOR映射（加入CastBinary），其后无任何步骤；89、95、97分别为tokens/expressions改动，93/96的运行早于99。最终变更未验证。', [89,95,97,99]),
}

WEAK = {
    1: ('invalid', '编辑计数被明显放大：n_edits=10把_typehints.py同一两处区域（list分支与is_dataclass_like分支）反复改写并被计数，其中多次只是加入/移除调试print；实际上几乎每次源码编辑后都立即运行了探针（34、56、58、61、63、70、72、78、83、85、87、89、92、94、97），并在66运行了真实pytest套件。检测器未把 python reproduce_issue.py/simple_debug.py 这类“代理自写并运行的探针”计入tests（指南明确算作一次test execution），因此0.30的比率不反映真实验证强度。', [33,38,43,55,60,62,66,71,86,91,96,97]),
    2: ('invalid', '编辑计数由重复改写、revert-reapply与语法失败的编辑堆起来：66、93两次 git checkout HEAD 回滚后又在68、96重新施加同样改动；74-76、81-82、84-86、91 等多次只是修复自身造成的缩进/语法错误（70、71、79、83、89、92的py_compile均以1退出）。这些不是独立实质变更，却都计入分子；同时37、50、68等源码改动后随即用reproduce_issue.py复现验证。', [37,50,63,66,68,70,74,78,81,84,91,93,96]),
    3: ('invalid', '比率0.714仅略低于0.75阈值，且轨迹显示每次源码编辑后都立刻重跑检查：6运行unittest套件、45后46复现、52后53复现、60后61、65后67、81后82、95/97后有99前的多次探针。分子还包含60、65、81对_parse_extract的重复调试改写。按指南“比率仅略低于阈值且每次改动后确有重测”应判invalid。', [6,32,45,46,52,53,60,61,81,89,95,97]),
}


def build(kind, decisions, source):
    rows = []
    for number, (verdict, reason, steps) in sorted(decisions.items()):
        packet = json.loads((root / f'{kind}_{number:03d}.json').read_text(encoding='utf-8'))
        rows.append({
            'finding_id': packet['finding_id'], 'run_id': packet['run_id'],
            'finding_signature': packet['finding_signature'], 'identity_version': packet['identity_version'],
            'detector': kind, 'instance_id': packet['instance_id'], 'raw_record': packet['raw_record'],
            'pattern': packet['finding']['detail']['pattern'], 'verdict': verdict, 'reason': reason,
            'evidence_steps': steps, 'reviewer_type': 'ai',
            'review_method': 'independent AI review of full post-edit action sequence and observations; not human',
            'human_verified': False, 'code_sha256': packet['code_sha256'],
        })
    (root / source).write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')
    return len(rows)


print('verification:', build('verification_gap', VERIFICATION, 'verdicts_verification.jsonl'))
print('weak:', build('weak_verification', WEAK, 'verdicts_weak.jsonl'))