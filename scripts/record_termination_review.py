"""Persist primary AI review decisions after reading termination evidence.

Decisions are explicitly enumerated by reviewed packet number, not inferred from
outcome labels or copied from detector output/old verdicts.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

root = Path('data/reports/review_v2')
valid_caps = [1,2,3,4,5,6,7,8,9,10,11,12,14,15,16,17,18,19,20,21,22,23,25,26,27,29,30,31,32,33,34,35,36,37,38,39,40]
decisions = {
    13: ('invalid', '最终diff明确修改src/zope/security/checker.py，增加_default_checkers[type({}.__repr__)] = _callableChecker，是安全检查器的实质源码变更；仅测试/scratch的断言错误。', [30,32,35,40], 'termination_anomaly_013_patch.diff:253-268'),
    24: ('valid', '完整最终diff只有reproduce_issue.py与test_edge_cases.py，内容均是调用PennyLane验证Barrier/WireCut的探针，没有pennylane库源码变更；符合patch_ignores_source的窄断言。迭代上限另有finding，不把所有此前调试都认作浪费。', [99], 'termination_anomaly_024_patch.diff:1-199'),
    28: ('invalid', '最终diff修改ignite/handlers/checkpoint.py：新增overwrite_existing参数并改变文件碰撞处理、saved列表更新逻辑，同时传递到ModelCheckpoint，明确是库源码；不能因checkpoint.py名称匹配check前缀就归为scratch。', [99], 'termination_anomaly_028_patch.diff:847-963'),
}
compact = {int(Path(r['packet']).stem.rsplit('_',1)[1]): r for r in map(json.loads,(root/'termination_compact.jsonl').read_text(encoding='utf-8').splitlines())}
rows = []
for n in sorted(valid_caps + list(decisions)):
    packet = json.loads((root/f'termination_anomaly_{n:03d}.json').read_text(encoding='utf-8'))
    if n in valid_caps:
        evidence = compact[n]
        assert evidence['total_steps'] == 100 and not evidence['submits'] and evidence['final_observation_empty']
        verdict, reason, steps, citation = ('valid', f"原始运行状态明确为达到100次迭代上限；归一化轨迹100步，未出现submit/finish，末步99的{packet['steps'][-1]['kind']}动作没有返回观测，与被预算截断一致。此判决仅确认终止异常，不判断补丁正确性或把此前步骤计为浪费。", [97,98,99], f'termination_compact.jsonl:{n}')
    else:
        verdict, reason, steps, citation = decisions[n]
    rows.append({k:packet[k] for k in ('finding_id','run_id','finding_signature','identity_version','instance_id','raw_record','code_sha256')} | {'detector':'termination_anomaly','pattern':packet['finding']['detail']['pattern'],'verdict':verdict,'reason':reason,'evidence_steps':steps,'evidence_reference':citation,'reviewer_type':'ai','review_method':'primary AI review of status, submission scan, final actions and actual patch; not human','human_verified':False})
(root/'verdicts_termination.jsonl').write_text('\n'.join(json.dumps(r,ensure_ascii=False) for r in rows)+'\n',encoding='utf-8')
print('Persisted reviewed termination decisions:',len(rows))
