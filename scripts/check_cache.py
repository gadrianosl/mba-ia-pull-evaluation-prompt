import json
from pathlib import Path
cache_path = Path('.cache/eval_cache.json')
dataset_path = Path('datasets/bug_to_user_story.jsonl')

if not cache_path.exists():
    print('NO_CACHE')
    raise SystemExit(1)

cache = json.loads(cache_path.read_text(encoding='utf-8'))
refs = set()
for k, v in cache.get('entries', {}).items():
    ref = v.get('reference', '')
    if ref:
        refs.add(ref.strip())

print('Cached references found:', len(refs))

with open(dataset_path, 'r', encoding='utf-8') as f:
    for i, line in enumerate(f, 1):
        try:
            obj = json.loads(line)
            ref = obj.get('outputs', {}).get('reference', '').strip()
        except Exception:
            ref = ''
        status = 'CACHED' if ref in refs else 'MISSING'
        print(f'#{i:02d} {status} {ref[:80].replace("\n"," ")}')
