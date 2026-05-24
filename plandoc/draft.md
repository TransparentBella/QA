
python3 - <<'PY'
import json
from pathlib import Path

path = Path('/data4/mjz/project/sport-artistic/IceArtBench/data/Skating_and_Composition_QA_Normalized.jsonl')

mapping = {
    'Composition': '节目编排',
    'Presentation': '表现力',
    'Skating_Skills': '滑行技术',
    'Ice Coverage': '冰面覆盖',
    'Musical Phrasing & Mapping': '音乐乐句处理与动作映射',
    'Originality & Creative Expression': '原创性与创造性表达',
    'Motion & Melody Synchrony': '动作与旋律同步性',
    'Limb&Body Coordination': '肢体与身体协调性',
    'Facial Expression & Involvement': '面部表情与投入度',
    'Movement & Emotional Projection': '动作表现与情感传达',
    'Musical Theme Interpretation': '音乐主题诠释',
    'Energy Variety & Contrast': '能量变化与对比',
    'Beat Precision & Timing': '节拍准确性与时机把握',
    'Core Engagement & Tension': '核心控制与身体张力',
    'Flow and Edge Glide': '流畅性与刃上滑行',
    'Posture and Body Control': '姿态与身体控制',
}

def normalize_category(value: str) -> str:
    text = str(value).strip()
    # 兼容 "9. Musical Theme Interpretation" 这类带编号类别
    if '. ' in text:
        prefix, rest = text.split('. ', 1)
        if prefix.strip().isdigit() and rest in mapping:
            return f'{prefix.strip()}. {mapping[rest]}'
    return mapping.get(text, text)

rows = []
changed_dimension = 0
changed_q_category = 0

with path.open('r', encoding='utf-8') as f:
    for line in f:
        if not line.strip():
            continue
        obj = json.loads(line)

        old_dimension = obj.get('dimension', '')
        new_dimension = mapping.get(old_dimension, old_dimension)
        if new_dimension != old_dimension:
            obj['dimension'] = new_dimension
            changed_dimension += 1

        old_q_category = obj.get('q_category', '')
        new_q_category = normalize_category(old_q_category)
        if new_q_category != old_q_category:
            obj['q_category'] = new_q_category
            changed_q_category += 1

        rows.append(obj)

with path.open('w', encoding='utf-8') as f:
    for obj in rows:
        f.write(json.dumps(obj, ensure_ascii=False, separators=(',', ':')) + '\n')

print({
    'rows': len(rows),
    'changed_dimension': changed_dimension,
    'changed_q_category': changed_q_category,
})
PY