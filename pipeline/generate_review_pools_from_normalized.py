#!/usr/bin/env python3
import argparse
import json
import shutil
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_SERVER_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = Path(
    # On mars this resolves to /data4/mjz/project/sport-artistic/IceArtBench/data.
    __import__('os').environ.get('ICEARTBENCH_DATA_DIR', str(DEFAULT_SERVER_ROOT / 'data'))
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Generate review_pools origin/dynamic files and SQLite DB from normalized IceArtBench JSONL.'
    )
    parser.add_argument(
        '--input',
        default=str(DEFAULT_DATA_DIR / 'Skating_and_Composition_QA_Normalized.jsonl'),
        help='Normalized JSONL input path.',
    )
    parser.add_argument(
        '--data-dir',
        default=str(DEFAULT_DATA_DIR),
        help='IceArtBench data dir. Used for DB, review_pools, and video_uri mapping.',
    )
    parser.add_argument(
        '--review-pools-dir',
        default=None,
        help='Output review_pools directory. Defaults to DATA_DIR/review_pools.',
    )
    parser.add_argument(
        '--db-path',
        default=None,
        help='SQLite DB path. Defaults to DATA_DIR/local_dev.db.',
    )
    parser.add_argument(
        '--clean-output',
        action='store_true',
        help='Delete existing origin/dynamic JSON files before generation.',
    )
    return parser.parse_args()


def _uuid() -> str:
    return str(uuid.uuid4())


def _utc_ms() -> int:
    return int(time.time() * 1000)


def _ms_to_iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def safe_file_part(value: str) -> str:
    cleaned = ''.join(ch if ch.isalnum() else '_' for ch in value.strip())
    while '__' in cleaned:
        cleaned = cleaned.replace('__', '_')
    return cleaned.strip('_') or 'unknown'


def make_video_uri(video_path: str, data_dir: Path) -> str:
    path = Path(video_path)
    try:
        rel = path.relative_to(data_dir)
    except ValueError as exc:
        raise ValueError(f'video_path is not under data_dir: {video_path}') from exc
    return '/media/' + rel.as_posix()


def make_file_key(video_id: str, q_category: str) -> str:
    return f'{safe_file_part(video_id)}__{safe_file_part(q_category)}'


def make_item_key(file_key: str, question_key: str) -> str:
    return f'{file_key}__{safe_file_part(question_key)}'


def ensure_dirs(review_pools_dir: Path, clean_output: bool) -> tuple[Path, Path]:
    origin_dir = review_pools_dir / 'origin'
    dynamic_dir = review_pools_dir / 'dynamic'

    if clean_output:
        for directory in (origin_dir, dynamic_dir):
            if directory.exists():
                shutil.rmtree(directory)

    origin_dir.mkdir(parents=True, exist_ok=True)
    dynamic_dir.mkdir(parents=True, exist_ok=True)
    return origin_dir, dynamic_dir


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
            video_path TEXT NOT NULL,
            video_uri TEXT NOT NULL,
            type TEXT NOT NULL,
            dimension TEXT NOT NULL,
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
        CREATE INDEX idx_review_items_type_dimension_category ON review_items(type, dimension, q_category);
        CREATE INDEX idx_review_items_video_id ON review_items(video_id);

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


def origin_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    first = rows[0]
    return {
        'video_path': first['video_path'],
        'video_uri': first['video_uri'],
        'video_id': first['video_id'],
        'type': first['type'],
        'dimension': first['dimension'],
        'q_category': first['q_category'],
        'questions': [
            {
                'question_key': row['question_key'],
                'question': row['question'],
                'options': row['options'],
                'answer': row['answer'],
            }
            for row in rows
        ],
    }


def dynamic_payload(rows: list[sqlite3.Row]) -> dict[str, Any]:
    first = rows[0]
    return {
        'video_path': first['video_path'],
        'video_uri': first['video_uri'],
        'video_id': first['video_id'],
        'type': first['type'],
        'dimension': first['dimension'],
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


def load_entries(input_path: Path, data_dir: Path, origin_dir: Path, dynamic_dir: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen_item_keys: dict[str, int] = {}

    with input_path.open('r', encoding='utf-8') as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            video_id = str(record['video_id']).strip()
            q_category = str(record['q_category']).strip()
            file_key = make_file_key(video_id, q_category)
            video_uri = make_video_uri(str(record['video_path']), data_dir)
            origin_path = origin_dir / f'{file_key}.json'
            dynamic_path = dynamic_dir / f'{file_key}.json'

            for question in record.get('questions') or []:
                question_key = str(question['question_key']).strip()
                item_key = make_item_key(file_key, question_key)
                if item_key in seen_item_keys:
                    first_line = seen_item_keys[item_key]
                    raise ValueError(
                        'Duplicate item_key found. '
                        f'first_line={first_line}, duplicate_line={line_number}, item_key={item_key}. '
                        'Fix the normalized/source JSONL manually and rerun normalization before generating review pools.'
                    )
                seen_item_keys[item_key] = line_number

                options = question.get('options') or []
                if not isinstance(options, list):
                    raise ValueError(f'options must be a list at input line {line_number}, question {question_key}')

                entries.append(
                    {
                        'file_key': file_key,
                        'item_key': item_key,
                        'question_key': question_key,
                        'video_id': video_id,
                        'video_path': record['video_path'],
                        'video_uri': video_uri,
                        'type': record['type'],
                        'dimension': record['dimension'],
                        'q_category': q_category,
                        'question': question['question'],
                        'options': options,
                        'answer': question['answer'],
                        'origin_path': str(origin_path),
                        'dynamic_path': str(dynamic_path),
                    }
                )

    entries.sort(key=lambda item: (item['dimension'], item['type'], item['q_category'], item['video_id'], item['question_key']))
    return entries


def write_origin_files(entries: list[dict[str, Any]]) -> int:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        grouped.setdefault(entry['file_key'], []).append(entry)

    written = 0
    for group_entries in grouped.values():
        path = Path(group_entries[0]['origin_path'])
        payload = origin_payload(group_entries)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        written += 1
    return written


def write_dynamic_group(conn: sqlite3.Connection, file_key: str) -> None:
    rows = conn.execute(
        'SELECT * FROM review_items WHERE file_key = ? ORDER BY question_key',
        (file_key,),
    ).fetchall()
    if not rows:
        return

    path = Path(rows[0]['dynamic_snapshot_path'])
    path.write_text(json.dumps(dynamic_payload(rows), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def import_entries(conn: sqlite3.Connection, entries: list[dict[str, Any]]) -> dict[str, int]:
    inserted = 0
    for entry in entries:
        now = _utc_ms()
        item_id = _uuid()
        conn.execute(
            '''
            INSERT INTO review_items (
                id, file_key, item_key, question_key, video_id, video_path, video_uri,
                type, dimension, q_category, question, options_json, answer,
                status, is_delete, is_modified, origin_snapshot_path, dynamic_snapshot_path,
                created_at_ms, updated_at_ms, modified_at_ms, modified_by
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, 0, ?, ?, ?, ?, NULL, NULL)
            ''',
            (
                item_id,
                entry['file_key'],
                entry['item_key'],
                entry['question_key'],
                entry['video_id'],
                entry['video_path'],
                entry['video_uri'],
                entry['type'],
                entry['dimension'],
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

    conn.commit()
    return {
        'inserted_review_items': inserted,
        'dynamic_files_written': len(file_keys),
    }


def main() -> None:
    args = parse_args()
    input_path = Path(args.input).resolve()
    data_dir = Path(args.data_dir).resolve()
    review_pools_dir = Path(args.review_pools_dir).resolve() if args.review_pools_dir else data_dir / 'review_pools'
    db_path = Path(args.db_path).resolve() if args.db_path else data_dir / 'local_dev.db'

    origin_dir, dynamic_dir = ensure_dirs(review_pools_dir, args.clean_output)
    entries = load_entries(input_path, data_dir, origin_dir, dynamic_dir)
    origin_written = write_origin_files(entries)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        ensure_review_schema(conn)
        result = import_entries(conn, entries)
    finally:
        conn.close()

    result.update(
        {
            'input': str(input_path),
            'data_dir': str(data_dir),
            'db_path': str(db_path),
            'origin_files_written': origin_written,
            'total_entries': len(entries),
            'unique_review_files': len({entry['file_key'] for entry in entries}),
        }
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
