#!/usr/bin/env python3
import argparse
import csv
import json
import re
import shlex
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


DEFAULT_VIDEO_ROOT = '/data4/mjz/project/sport-artistic/IceArtBench/data/video'
VIDEO_EXTENSIONS = {'.mp4', '.webm'}
LETTER_PREFIX_RE = re.compile(r'^\s*([A-Da-d])(?:[.\u3001\uff0e]|\s+)\s*(.*)$')
SAFE_KEY_RE = re.compile(r'[^0-9A-Za-z]+')


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Normalize Skating_and_Composition_QA_Final.jsonl for IceArtBench annotation.'
    )
    parser.add_argument('--input', default='Skating_and_Composition_QA_Final.jsonl')
    parser.add_argument('--path-md', default='path.md')
    parser.add_argument('--output', default='data/Skating_and_Composition_QA_Normalized.jsonl')
    parser.add_argument('--summary', default='data/qa_normalization_summary.json')
    parser.add_argument('--reports-dir', default='pipeline/reports')
    parser.add_argument('--video-root', default=DEFAULT_VIDEO_ROOT)
    return parser.parse_args()


def strip_option_prefix(value: str) -> str:
    text = str(value).strip()
    match = LETTER_PREFIX_RE.match(text)
    if match:
        return match.group(2).strip()
    return text


def option_label(value: str) -> str | None:
    match = LETTER_PREFIX_RE.match(str(value))
    if not match:
        return None
    return match.group(1).upper()


def normalize_label(value: Any) -> str:
    text = str(value).strip()
    match = LETTER_PREFIX_RE.match(text)
    if match:
        return match.group(1).upper()
    return text.rstrip('.\u3001\uff0e').strip().upper()


def option_items(raw_options: Any) -> list[tuple[str | None, str, str]]:
    if isinstance(raw_options, dict):
        items: list[tuple[str | None, str, str]] = []
        for key, value in raw_options.items():
            label = normalize_label(key)
            text = str(value).strip()
            items.append((label or None, text, text))
        return items

    if isinstance(raw_options, list):
        items = []
        for value in raw_options:
            raw = str(value)
            items.append((option_label(raw), strip_option_prefix(raw), raw))
        return items

    return []


def normalize_for_compare(value: str) -> str:
    return re.sub(r'\s+', '', strip_option_prefix(value)).strip()


def safe_key(value: str) -> str:
    cleaned = SAFE_KEY_RE.sub('_', value.strip())
    cleaned = re.sub(r'_+', '_', cleaned).strip('_')
    return cleaned or 'Question'


def unique_question_key(base: str, seen: set[str]) -> str:
    if base not in seen:
        seen.add(base)
        return base

    index = 2
    while f'{base}_dup{index}' in seen:
        index += 1
    key = f'{base}_dup{index}'
    seen.add(key)
    return key


def extract_video_tokens(line: str) -> list[str]:
    try:
        tokens = shlex.split(line)
    except ValueError:
        tokens = line.split()

    files: list[str] = []
    for token in tokens:
        cleaned = token.strip().strip("'\"")
        suffix = Path(cleaned).suffix.lower()
        if suffix in VIDEO_EXTENSIONS:
            files.append(cleaned)
    return files


def parse_video_index(path_md: Path, video_root: str) -> dict[str, list[str]]:
    index: dict[str, list[str]] = defaultdict(list)
    current_dir: str | None = None

    if not path_md.exists():
        return index

    prompt_dir_re = re.compile(r'IceArtBench/data/video(?:/([^#\s]+))?#')
    ls_dir_re = re.compile(r'\bls\s+([A-Za-z0-9_-]+)\s*$')

    for raw_line in path_md.read_text(encoding='utf-8').splitlines():
        line = raw_line.rstrip()
        prompt_match = prompt_dir_re.search(line)
        if prompt_match:
            current_dir = prompt_match.group(1)

        ls_match = ls_dir_re.search(line)
        if ls_match:
            current_dir = ls_match.group(1)
            continue

        if current_dir is None:
            continue

        for file_name in extract_video_tokens(line):
            stem = Path(file_name).stem.strip()
            if not stem:
                continue
            rel_path = f'{current_dir}/{file_name.strip()}'
            abs_path = f'{video_root.rstrip("/")}/{rel_path}'
            if abs_path not in index[stem]:
                index[stem].append(abs_path)

    return dict(index)


def infer_video_id(record: dict[str, Any]) -> str:
    video_id = str(record.get('video_id') or '').strip()
    if video_id:
        return video_id

    video_path = str(record.get('video_path') or '').strip()
    if video_path:
        return Path(video_path).stem

    return ''


def preferred_dirs(record: dict[str, Any]) -> list[str]:
    values = [
        str(record.get('video_path') or ''),
        str(record.get('dimension') or ''),
        str(record.get('q_category') or ''),
        str(record.get('task') or ''),
    ]
    haystack = ' '.join(values).lower()
    dirs: list[str] = []

    if 'stsq' in haystack or 'skating_skills' in haystack or 'skating skills' in haystack:
        dirs.append('stsq')
    if 'chsq' in haystack:
        dirs.append('Chsq')
    if 'jump' in haystack:
        dirs.append('Jump')
    if 'music' in haystack or 'composition' in haystack:
        dirs.append('music')
    if 'spatial' in haystack:
        dirs.append('spatial')
    if 'face' in haystack or 'presentation' in haystack:
        dirs.append('face')

    deduped: list[str] = []
    for item in dirs:
        if item not in deduped:
            deduped.append(item)
    return deduped


def candidate_dir(path: str, video_root: str) -> str:
    rel = path.replace(video_root.rstrip('/') + '/', '', 1)
    return rel.split('/', 1)[0]


def resolve_video_path(
    record: dict[str, Any],
    video_id: str,
    video_index: dict[str, list[str]],
    video_root: str,
) -> tuple[str, str, list[str]]:
    candidates = video_index.get(video_id, [])
    original = str(record.get('video_path') or '').strip()

    if not candidates:
        return original, 'unresolved', []

    if len(candidates) == 1:
        return candidates[0], 'matched', candidates

    prefs = preferred_dirs(record)
    for preferred in prefs:
        matches = [path for path in candidates if candidate_dir(path, video_root).lower() == preferred.lower()]
        if len(matches) == 1:
            return matches[0], 'matched_by_context', candidates

    return original, 'ambiguous', candidates


def remove_duplicate_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def normalize_question(
    question: dict[str, Any],
    *,
    q_category: str,
    question_index: int,
    seen_keys: set[str],
    record_index: int,
    video_id: str,
    reports: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    raw_key = str(question.get('question_key') or '').strip()
    generated_key = f'{safe_key(q_category)}-{question_index}'
    question_key = unique_question_key(raw_key or generated_key, seen_keys)
    if raw_key and question_key != raw_key:
        reports['question_key_conflicts'].append(
            {
                'line': record_index,
                'video_id': video_id,
                'q_category': q_category,
                'original_question_key': raw_key,
                'final_question_key': question_key,
            }
        )
    elif not raw_key:
        reports['question_key_generated'].append(
            {
                'line': record_index,
                'video_id': video_id,
                'q_category': q_category,
                'final_question_key': question_key,
            }
        )

    question_text = str(question.get('question') or '').strip()

    if 'ground_truth' in question or 'distractors' in question:
        answer = str(question.get('ground_truth') or question.get('answer') or '').strip()
        raw_options = question.get('distractors') or question.get('options') or []
        options = [text for _, text, _ in option_items(raw_options)]
        options = remove_duplicate_preserve_order([option.strip() for option in options if option.strip()])
    else:
        raw_options = question.get('options') or []
        options_with_labels = option_items(raw_options)
        option_by_label = {label: text for label, text, _ in options_with_labels if label}
        raw_answer = str(question.get('answer') or '').strip()
        raw_correct = str(question.get('correct_option') or question.get('correct_answer') or '').strip()
        answer_label = normalize_label(raw_answer) if normalize_label(raw_answer) in option_by_label else ''
        correct_option = normalize_label(raw_correct) if normalize_label(raw_correct) in option_by_label else ''

        if answer_label:
            answer = option_by_label[answer_label]
            correct_option = answer_label
        elif correct_option:
            answer = option_by_label[correct_option]
        elif raw_answer:
            answer = raw_answer
        else:
            answer = raw_correct

        if not answer and correct_option:
            answer = next((text for label, text, _ in options_with_labels if label == correct_option), '')

        removed = False
        filtered: list[str] = []
        for label, option_text, raw_option in options_with_labels:
            label_matches = bool(correct_option and label == correct_option)
            answer_matches = bool(answer and normalize_for_compare(raw_option) == normalize_for_compare(answer))
            if not removed and (label_matches or answer_matches):
                removed = True
                continue
            filtered.append(option_text)

        if raw_options and not removed and ('correct_option' in question or 'correct_answer' in question):
            reports['correct_option_unresolved'].append(
                {
                    'line': record_index,
                    'video_id': video_id,
                    'q_category': q_category,
                    'question_key': question_key,
                    'correct_option': correct_option,
                    'answer': answer,
                    'options_count': len(raw_options),
                }
            )

        options = remove_duplicate_preserve_order([option.strip() for option in filtered if option.strip()])

    final_question = {
        'question_key': question_key,
        'question': question_text,
        'options': options,
        'answer': answer,
    }

    missing = [key for key, value in final_question.items() if value in ('', [])]
    if missing:
        reports['schema_anomalies'].append(
            {
                'line': record_index,
                'video_id': video_id,
                'q_category': q_category,
                'question_key': question_key,
                'missing_fields': '|'.join(missing),
            }
        )

    return final_question


def normalize_record(
    record: dict[str, Any],
    *,
    record_index: int,
    video_index: dict[str, list[str]],
    video_root: str,
    reports: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    video_id = infer_video_id(record)
    task = str(record.get('task') or '').strip()
    raw_q_category = str(record.get('q_category') or '').strip()
    raw_category = str(record.get('category') or '').strip()
    q_category = task or raw_q_category or raw_category

    final_video_path, path_status, candidates = resolve_video_path(record, video_id, video_index, video_root)
    source_video_path = str(record.get('video_path') or '').strip()

    normalized = {
        'video_path': final_video_path,
        'video_id': video_id,
        'type': str(record.get('type') or '').strip(),
        'dimension': str(record.get('dimension') or '').strip(),
        'q_category': q_category,
        'questions': [],
    }

    if path_status == 'matched_by_context' and source_video_path and final_video_path and source_video_path != final_video_path:
        normalized['source_video_path'] = source_video_path

    if task and raw_q_category and task != raw_q_category:
        normalized['source_q_category'] = raw_q_category
        reports['q_category_conflicts'].append(
            {
                'line': record_index,
                'video_id': video_id,
                'task': task,
                'q_category': raw_q_category,
                'final_q_category': q_category,
            }
        )

    if path_status == 'unresolved':
        reports['video_path_unresolved'].append(
            {
                'line': record_index,
                'video_id': video_id,
                'source_video_path': source_video_path,
            }
        )
    elif path_status == 'matched_by_context':
        reports['video_path_context_resolved'].append(
            {
                'line': record_index,
                'video_id': video_id,
                'source_video_path': source_video_path,
                'final_video_path': final_video_path,
                'candidates': '|'.join(candidates),
            }
        )
    elif path_status == 'ambiguous':
        reports['video_path_ambiguous'].append(
            {
                'line': record_index,
                'video_id': video_id,
                'source_video_path': source_video_path,
                'candidates': '|'.join(candidates),
            }
        )

    seen_keys: set[str] = set()
    for index, question in enumerate(record.get('questions') or [], start=1):
        if isinstance(question, dict):
            normalized['questions'].append(
                normalize_question(
                    question,
                    q_category=q_category,
                    question_index=index,
                    seen_keys=seen_keys,
                    record_index=record_index,
                    video_id=video_id,
                    reports=reports,
                )
            )

    missing_top = [
        key
        for key in ('video_path', 'video_id', 'type', 'dimension', 'q_category', 'questions')
        if normalized.get(key) in ('', [])
    ]
    if missing_top:
        reports['schema_anomalies'].append(
            {
                'line': record_index,
                'video_id': video_id,
                'q_category': q_category,
                'question_key': '',
                'missing_fields': '|'.join(missing_top),
            }
        )

    return normalized


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text('', encoding='utf-8')
        return

    fieldnames = sorted({key for row in rows for key in row.keys()})
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)
    summary_path = Path(args.summary)
    reports_dir = Path(args.reports_dir)
    video_index = parse_video_index(Path(args.path_md), args.video_root)

    reports: dict[str, list[dict[str, Any]]] = defaultdict(list)
    normalized_records: list[dict[str, Any]] = []
    question_shape_counts: Counter[str] = Counter()

    with input_path.open('r', encoding='utf-8') as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                reports['json_decode_errors'].append({'line': line_number, 'error': str(exc)})
                continue

            for question in record.get('questions') or []:
                if isinstance(question, dict):
                    if 'ground_truth' in question or 'distractors' in question:
                        question_shape_counts['ground_truth_distractors'] += 1
                    elif 'correct_option' in question:
                        question_shape_counts['options_answer_correct_option'] += 1
                    else:
                        question_shape_counts['options_answer'] += 1

            normalized_records.append(
                normalize_record(
                    record,
                    record_index=line_number,
                    video_index=video_index,
                    video_root=args.video_root,
                    reports=reports,
                )
            )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open('w', encoding='utf-8') as handle:
        for record in normalized_records:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(',', ':')))
            handle.write('\n')

    report_names = [
        'video_path_unresolved',
        'video_path_context_resolved',
        'video_path_ambiguous',
        'q_category_conflicts',
        'correct_option_unresolved',
        'question_key_conflicts',
        'question_key_generated',
        'schema_anomalies',
        'json_decode_errors',
    ]
    for name in report_names:
        write_csv(reports_dir / f'{name}.csv', reports[name])

    summary = {
        'input': str(input_path),
        'output': str(output_path),
        'records_written': len(normalized_records),
        'video_index_stems': len(video_index),
        'video_index_paths': sum(len(paths) for paths in video_index.values()),
        'question_shapes': dict(question_shape_counts),
        'reports': {name: len(reports[name]) for name in report_names},
    }

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
