import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
MEDIA_CLIPS_DIR = os.path.join(BASE_DIR, 'media', 'clips')
POOL_PATH = os.path.join(DATA_DIR, 'question_pool.json')
DB_PATH = os.path.join(DATA_DIR, 'local_dev.db')
REVIEW_POOLS_DIR = os.path.join(DATA_DIR, 'review_pools')
ORIGIN_DIR = os.path.join(REVIEW_POOLS_DIR, 'origin')
DYNAMIC_DIR = os.path.join(REVIEW_POOLS_DIR, 'dynamic')

TYPE_BY_PREFIX = {
    'j': 'MS',
    'c': 'FS',
}


def _uuid() -> str:
    return str(uuid.uuid4())


def _utc_ms() -> int:
    return int(time.time() * 1000)


def _ms_to_iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _sorted_choice_keys(question: dict) -> list[str]:
    keys = [k for k in question.keys() if k.startswith('choice')]

    def key_num(k: str) -> int:
        suffix = k[len('choice') :]
        return int(suffix) if suffix.isdigit() else 10**9

    return sorted(keys, key=key_num)


def _pool_category_key(pool_name: str) -> str:
    return ''.join(ch if ch.isalnum() else '_' for ch in pool_name.strip()).upper()


def _strip_mp4(name: str) -> str:
    return name[:-4] if name.lower().endswith('.mp4') else name


def _video_subdir(file_name: str) -> str | None:
    lower = file_name.lower()
    if lower.startswith('j'):
        return 'j'
    if lower.startswith('c'):
        return 'c'
    return None


def _video_uri(file_name: str) -> str:
    subdir = _video_subdir(file_name)
    if subdir:
        return f'/media/clips/{subdir}/{file_name}'
    return f'/media/clips/{file_name}'


def _infer_type(video_file: str) -> str:
    prefix = video_file[:1].lower()
    return TYPE_BY_PREFIX.get(prefix, 'UNKNOWN')


def _ensure_dirs() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(ORIGIN_DIR, exist_ok=True)
    os.makedirs(DYNAMIC_DIR, exist_ok=True)


def transform_question_pool(old_data: list[dict]) -> list[dict]:
    new_pools: list[dict] = []

    for pool in old_data:
        pool_name = pool['pool_name']
        questions = pool.get('questions', [])
        pool_options: list[dict] = []
        new_questions: list[dict] = []

        for qi, q in enumerate(questions):
            qid = q['id']
            stem = q['stem']
            choice_keys = _sorted_choice_keys(q)
            choices = [q[k] for k in choice_keys]
            labels = [c['label'] for c in choices]
            unique_sorted_labels = sorted(set(labels))
            if not unique_sorted_labels:
                raise ValueError(f'Question {qid} has no choices')

            correct_label = unique_sorted_labels[qi % len(unique_sorted_labels)]
            correct_option_id = None

            for c in choices:
                video_file = c['label']
                option_id = f"{qid}__{_strip_mp4(video_file)}"
                pool_options.append(
                    {
                        'option_id': option_id,
                        'video_file': video_file,
                        'text': c['text'],
                        'meta': {'question_id': qid},
                    }
                )
                if video_file == correct_label:
                    correct_option_id = option_id

            if correct_option_id is None:
                correct_option_id = f"{qid}__{_strip_mp4(unique_sorted_labels[0])}"

            new_questions.append(
                {
                    'question_id': qid,
                    'stem': stem,
                    'correct_option_id': correct_option_id,
                }
            )

        new_pools.append(
            {
                'pool_name': pool_name,
                'category_key': _pool_category_key(pool_name),
                'options': pool_options,
                'questions': new_questions,
            }
        )

    return new_pools


def _build_review_entries(pools: list[dict]) -> list[dict]:
    entries: list[dict] = []
    seen_item_keys: set[str] = set()

    for pool in pools:
        q_category = pool['pool_name']
        options_by_question: dict[str, list[dict]] = {}
        video_files_in_pool: set[str] = set()

        for opt in pool.get('options', []):
            qid = (opt.get('meta') or {}).get('question_id')
            if not qid:
                continue
            options_by_question.setdefault(qid, []).append(opt)
            video_files_in_pool.add(opt['video_file'])

        for video_file in sorted(video_files_in_pool):
            video_id = _strip_mp4(video_file)
            file_key = f'{video_id}__{q_category}'

            for question in pool.get('questions', []):
                question_id = question['question_id']
                stem = question['stem']
                all_options = options_by_question.get(question_id, [])
                answer_option = next((opt for opt in all_options if opt['video_file'] == video_file), None)
                if not answer_option:
                    continue

                distractor_options = [opt for opt in all_options if opt['video_file'] != video_file]
                if len(distractor_options) < 3:
                    raise ValueError(f'Question {question_id} for video {video_file} has fewer than 3 distractors')

                item_key = f'{file_key}__{question_id}'
                if item_key in seen_item_keys:
                    raise ValueError(f'Duplicate review item key generated: {item_key}')
                seen_item_keys.add(item_key)

                entries.append(
                    {
                        'file_key': file_key,
                        'item_key': item_key,
                        'question_key': question_id,
                        'video_id': video_id,
                        'video_file': video_file,
                        'video_uri': _video_uri(video_file),
                        'type': _infer_type(video_file),
                        'q_category': q_category,
                        'question': stem,
                        'options': [opt['text'] for opt in distractor_options[:3]],
                        'answer': answer_option['text'],
                        'origin_path': os.path.join(ORIGIN_DIR, f'{file_key}.json'),
                        'dynamic_path': os.path.join(DYNAMIC_DIR, f'{file_key}.json'),
                    }
                )

    entries.sort(key=lambda item: (item['type'], item['q_category'], item['video_id'], item['question_key']))
    return entries


def ensure_review_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        '''
        PRAGMA foreign_keys = ON;

        DROP TABLE IF EXISTS review_actions;
        DROP TABLE IF EXISTS review_items;

        CREATE TABLE review_items (
            id TEXT PRIMARY KEY,
            file_key TEXT NOT NULL,
            item_key TEXT NOT NULL UNIQUE,
            question_key TEXT NOT NULL,
            video_id TEXT NOT NULL,
            video_uri TEXT NOT NULL,
            type TEXT NOT NULL,
            q_category TEXT NOT NULL,
            question TEXT NOT NULL,
            options_json TEXT NOT NULL,
            answer TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending', 'passed', 'modified', 'deleted')),
            is_delete INTEGER NOT NULL DEFAULT 0,
            is_modified INTEGER NOT NULL DEFAULT 0,
            origin_snapshot_path TEXT NOT NULL,
            dynamic_snapshot_path TEXT NOT NULL,
            created_at_ms INTEGER NOT NULL,
            updated_at_ms INTEGER NOT NULL,
            modified_at_ms INTEGER,
            modified_by TEXT
        );

        CREATE INDEX idx_review_items_file_key ON review_items(file_key);
        CREATE INDEX idx_review_items_type_category ON review_items(type, q_category);

        CREATE TABLE review_actions (
            id TEXT PRIMARY KEY,
            review_item_id TEXT NOT NULL,
            action_type TEXT NOT NULL CHECK (action_type IN ('INIT', 'PASS', 'MODIFY', 'DELETE')),
            before_answer TEXT,
            after_answer TEXT,
            operator_id TEXT,
            operator_name TEXT,
            created_at_ms INTEGER NOT NULL,
            FOREIGN KEY(review_item_id) REFERENCES review_items(id)
        );
        '''
    )


def _origin_group_payload(rows: list[sqlite3.Row | dict]) -> dict:
    first = rows[0]
    return {
        'video_id': first['video_id'],
        'type': first['type'],
        'q_category': first['q_category'],
        'questions': [
            {
                'question_key': row['question_key'],
                'question': row['question'],
                'options': json.loads(row['options_json']) if 'options_json' in row and isinstance(row['options_json'], str) else row['options'],
                'answer': row['answer'],
            }
            for row in rows
        ],
    }


def _dynamic_group_payload(rows: list[sqlite3.Row]) -> dict:
    first = rows[0]
    return {
        'video_id': first['video_id'],
        'type': first['type'],
        'q_category': first['q_category'],
        'questions': [
            {
                'question_key': row['question_key'],
                'question': row['question'],
                'options': json.loads(row['options_json']),
                'answer': row['answer'],
                'status': row['status'],
                'is_delete': bool(row['is_delete']),
                'is_modified': bool(row['is_modified']),
                '_modifiedAt': _ms_to_iso(row['modified_at_ms']),
                '_modified_by': row['modified_by'],
            }
            for row in rows
        ],
    }


def write_origin_files(entries: list[dict]) -> int:
    grouped: dict[str, list[dict]] = {}
    for entry in entries:
        grouped.setdefault(entry['file_key'], []).append(entry)

    created_or_updated = 0
    for file_key, group_entries in grouped.items():
        origin_path = group_entries[0]['origin_path']
        payload = _origin_group_payload(group_entries)
        with open(origin_path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write('\n')
        created_or_updated += 1
    return created_or_updated


def write_dynamic_group(conn: sqlite3.Connection, file_key: str) -> None:
    cur = conn.execute(
        'SELECT * FROM review_items WHERE file_key = ? ORDER BY question_key',
        (file_key,),
    )
    rows = cur.fetchall()
    if not rows:
        return
    payload = _dynamic_group_payload(rows)
    path = rows[0]['dynamic_snapshot_path']
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write('\n')


def import_review_items(conn: sqlite3.Connection, entries: list[dict]) -> dict:
    conn.row_factory = sqlite3.Row
    inserted = 0
    dynamic_written = 0

    for entry in entries:
        now = _utc_ms()
        item_id = _uuid()
        conn.execute(
            '''
            INSERT INTO review_items (
                id, file_key, item_key, question_key, video_id, video_uri, type, q_category, question,
                options_json, answer, status, is_delete, is_modified,
                origin_snapshot_path, dynamic_snapshot_path,
                created_at_ms, updated_at_ms, modified_at_ms, modified_by
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, 0, ?, ?, ?, ?, NULL, NULL)
            ''',
            (
                item_id,
                entry['file_key'],
                entry['item_key'],
                entry['question_key'],
                entry['video_id'],
                entry['video_uri'],
                entry['type'],
                entry['q_category'],
                entry['question'],
                json.dumps(entry['options'], ensure_ascii=False),
                entry['answer'],
                entry['origin_path'],
                entry['dynamic_path'],
                now,
                now,
            ),
        )
        conn.execute(
            '''
            INSERT INTO review_actions (
                id, review_item_id, action_type, before_answer, after_answer, operator_id, operator_name, created_at_ms
            )
            VALUES (?, ?, 'INIT', NULL, ?, NULL, 'system', ?)
            ''',
            (_uuid(), item_id, entry['answer'], now),
        )
        inserted += 1

    file_keys = sorted({entry['file_key'] for entry in entries})
    for file_key in file_keys:
        write_dynamic_group(conn, file_key)
        dynamic_written += 1

    conn.commit()
    return {
        'inserted_review_items': inserted,
        'origin_files_written': len(file_keys),
        'dynamic_files_written': dynamic_written,
        'total_entries': len(entries),
    }


def main() -> None:
    _ensure_dirs()

    with open(POOL_PATH, 'r', encoding='utf-8') as f:
        raw = json.load(f)

    if isinstance(raw, list) and raw and 'options' in raw[0] and 'category_key' in raw[0]:
        transformed = raw
    else:
        transformed = transform_question_pool(raw)
        with open(POOL_PATH, 'w', encoding='utf-8') as f:
            json.dump(transformed, f, ensure_ascii=False, indent=2)
            f.write('\n')

    entries = _build_review_entries(transformed)
    origin_written = write_origin_files(entries)

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        ensure_review_schema(conn)
        result = import_review_items(conn, entries)
    finally:
        conn.close()

    result['origin_files_written'] = origin_written
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
