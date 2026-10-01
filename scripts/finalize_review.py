"""Validate complete versioned AI review coverage and persist independent labels.

This assembles decisions, never generates verdicts or reuses legacy labels.
"""
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.evaluate import live_findings


def main():
    root = Path('data/reports/review_v2')
    manifest = json.loads((root/'manifest.json').read_text(encoding='utf-8'))
    current_sha = hashlib.sha256(b''.join(str(p).encode()+p.read_bytes() for p in sorted(Path('trajdx').rglob('*.py')))).hexdigest()
    if current_sha != manifest['code_sha256']:
        raise ValueError('Runtime code changed since evidence freeze; re-review required')
    expected = {row['finding_id']: row for row in manifest['findings']}
    verdicts = []
    for name in ('verdicts_termination.jsonl','verdicts_verification.jsonl','verdicts_weak.jsonl','verdicts_search_loop.jsonl'):
        verdicts.extend(json.loads(line) for line in (root/name).read_text(encoding='utf-8-sig').splitlines() if line.strip())
    counts = Counter(row['finding_id'] for row in verdicts)
    if set(counts) != set(expected) or any(n != 1 for n in counts.values()):
        raise ValueError('Review coverage missing, extra or duplicate IDs')
    live, duplicates = live_findings(Path('data/raw/openhands_sample.jsonl'))
    if set(live) != set(expected) or duplicates:
        raise ValueError('Live claims do not equal frozen claims')
    for row in verdicts:
        if row['verdict'] not in ('valid','invalid','uncertain'):
            raise ValueError('Unknown verdict')
        if row.get('reviewer_type') != 'ai' or not row.get('reason') or not row.get('evidence_steps'):
            raise ValueError('Missing AI provenance, reason or evidence')
        for key in ('run_id','finding_signature'):
            if row[key] != expected[row['finding_id']][key]:
                raise ValueError(f'Identity mismatch: {key}')
        row['human_verified'] = False
        row['code_sha256'] = manifest['code_sha256']
        row['raw_sha256'] = manifest['raw_sha256']
    verdicts.sort(key=lambda r:(r['raw_record'],r['detector'],r['finding_id']))
    destination = Path('data/labels/reviewed_ai_identity_v2.jsonl')
    body = ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in verdicts)
    if destination.exists():
        existing = destination.read_text(encoding='utf-8-sig')
        if existing != body:
            raise ValueError(
                'Refusing to overwrite an existing independent review with different '
                'verdicts: re-review is a human decision, not a script side effect'
            )
        print('existing review is identical; nothing to rewrite')
    destination.write_text(body, encoding='utf-8')
    by_detector = defaultdict(Counter)
    by_pattern = defaultdict(Counter)
    for row in verdicts:
        by_detector[row['detector']][row['verdict']] += 1
        by_pattern[row['detector']+'/'+row['pattern']][row['verdict']] += 1
    report = {'finding_count':len(verdicts),'by_detector':dict(by_detector),'by_pattern':dict(by_pattern),
              'reviewer_type':'ai','human_verified':False,'code_sha256':current_sha,'raw_sha256':manifest['raw_sha256'],
              'caveat':'Independent AI context review, not human ground truth or independent held-out evaluation. No old labels reused; task outcomes excluded from review packets.'}
    (root/'review_summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
