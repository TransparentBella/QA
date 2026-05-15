import json
import os
import sqlite3
import uuid


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
MEDIA_CLIPS_DIR = os.path.join(BASE_DIR, "media", "clips")
POOL_PATH = os.path.join(DATA_DIR, "question_pool.json")
DB_PATH = os.path.join(DATA_DIR, "local_dev.db")


def _uuid() -> str:
    return str(uuid.uuid4())


def _sorted_choice_keys(question: dict) -> list[str]:
    keys = [k for k in question.keys() if k.startswith("choice")]

    def key_num(k: str) -> int:
        suffix = k[len("choice") :]
        return int(suffix) if suffix.isdigit() else 10**9

    return sorted(keys, key=key_num)


def _pool_category_key(pool_name: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in pool_name.strip()).upper()


def _strip_mp4(name: str) -> str:
    return name[:-4] if name.lower().endswith(".mp4") else name


def _video_subdir(file_name: str) -> str | None:
    lower = file_name.lower()
    if lower.startswith("j"):
        return "j"
    if lower.startswith("c"):
        return "c"
    return None


def _video_local_path(file_name: str) -> str:
    subdir = _video_subdir(file_name)
    if subdir:
        return os.path.join(MEDIA_CLIPS_DIR, subdir, file_name)
    return os.path.join(MEDIA_CLIPS_DIR, file_name)


def _video_uri(file_name: str) -> str:
    subdir = _video_subdir(file_name)
    if subdir:
        return f"/media/clips/{subdir}/{file_name}"
    return f"/media/clips/{file_name}"


def transform_question_pool(old_data: list[dict]) -> list[dict]:
    new_pools: list[dict] = []

    for pool in old_data:
        pool_name = pool["pool_name"]
        questions = pool.get("questions", [])
        pool_options: list[dict] = []
        new_questions: list[dict] = []

        for qi, q in enumerate(questions):
            qid = q["id"]
            stem = q["stem"]
            choice_keys = _sorted_choice_keys(q)
            choices = [q[k] for k in choice_keys]
            labels = [c["label"] for c in choices]
            unique_sorted_labels = sorted(set(labels))
            if not unique_sorted_labels:
                raise ValueError(f"Question {qid} has no choices")

            correct_label = unique_sorted_labels[qi % len(unique_sorted_labels)]
            correct_option_id = None

            for c in choices:
                video_file = c["label"]
                option_id = f"{qid}__{_strip_mp4(video_file)}"
                pool_options.append(
                    {
                        "option_id": option_id,
                        "video_file": video_file,
                        "text": c["text"],
                        "meta": {"question_id": qid},
                    }
                )
                if video_file == correct_label:
                    correct_option_id = option_id

            if correct_option_id is None:
                correct_option_id = f"{qid}__{_strip_mp4(unique_sorted_labels[0])}"

            new_questions.append(
                {
                    "question_id": qid,
                    "stem": stem,
                    "correct_option_id": correct_option_id,
                }
            )

        new_pools.append(
            {
                "pool_name": pool_name,
                "category_key": _pool_category_key(pool_name),
                "options": pool_options,
                "questions": new_questions,
            }
        )

    return new_pools


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS pools (
            id TEXT PRIMARY KEY,
            pool_name TEXT NOT NULL UNIQUE,
            category_key TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS videos (
            id TEXT PRIMARY KEY,
            file_name TEXT NOT NULL UNIQUE,
            local_path TEXT,
            uri TEXT NOT NULL,
            is_available INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS options (
            id TEXT PRIMARY KEY,
            pool_id TEXT NOT NULL,
            external_option_id TEXT NOT NULL UNIQUE,
            video_id TEXT NOT NULL,
            label TEXT NOT NULL,
            text TEXT NOT NULL,
            meta_json TEXT,
            FOREIGN KEY(pool_id) REFERENCES pools(id),
            FOREIGN KEY(video_id) REFERENCES videos(id)
        );

        CREATE TABLE IF NOT EXISTS questions (
            id TEXT PRIMARY KEY,
            pool_id TEXT NOT NULL,
            external_question_id TEXT NOT NULL UNIQUE,
            stem TEXT NOT NULL,
            correct_option_id TEXT NOT NULL,
            FOREIGN KEY(pool_id) REFERENCES pools(id),
            FOREIGN KEY(correct_option_id) REFERENCES options(id)
        );

        CREATE INDEX IF NOT EXISTS idx_options_pool_id ON options(pool_id);
        CREATE INDEX IF NOT EXISTS idx_questions_pool_id ON questions(pool_id);
        """
    )


def import_into_sqlite(conn: sqlite3.Connection, pools: list[dict]) -> dict:
    conn.row_factory = sqlite3.Row

    pool_id_by_name: dict[str, str] = {}
    video_id_by_file: dict[str, str] = {}
    option_db_id_by_external: dict[str, str] = {}

    inserted = {"pools": 0, "videos": 0, "options": 0, "questions": 0}
    missing_videos: list[str] = []
    deleted = {"orphan_options": 0, "orphan_videos": 0}

    for pool in pools:
        pool_name = pool["pool_name"]
        category_key = pool["category_key"]
        current_external_option_ids: set[str] = set()

        cur = conn.execute("SELECT id FROM pools WHERE pool_name = ?", (pool_name,))
        row = cur.fetchone()
        if row:
            pool_id = row["id"]
            conn.execute(
                "UPDATE pools SET category_key = ? WHERE id = ?",
                (category_key, pool_id),
            )
        else:
            pool_id = _uuid()
            conn.execute(
                "INSERT INTO pools (id, pool_name, category_key) VALUES (?, ?, ?)",
                (pool_id, pool_name, category_key),
            )
            inserted["pools"] += 1
        pool_id_by_name[pool_name] = pool_id

        for opt in pool.get("options", []):
            file_name = opt["video_file"]
            if file_name in video_id_by_file:
                continue

            local_path = _video_local_path(file_name)
            is_available = 1 if os.path.isfile(local_path) else 0
            if not is_available:
                missing_videos.append(file_name)
                local_path = None

            cur = conn.execute("SELECT id FROM videos WHERE file_name = ?", (file_name,))
            row = cur.fetchone()
            if row:
                video_id = row["id"]
                conn.execute(
                    "UPDATE videos SET local_path = ?, uri = ?, is_available = ? WHERE id = ?",
                    (local_path, _video_uri(file_name), is_available, video_id),
                )
            else:
                video_id = _uuid()
                conn.execute(
                    "INSERT INTO videos (id, file_name, local_path, uri, is_available) VALUES (?, ?, ?, ?, ?)",
                    (video_id, file_name, local_path, _video_uri(file_name), is_available),
                )
                inserted["videos"] += 1

            video_id_by_file[file_name] = video_id

        for opt in pool.get("options", []):
            external_option_id = opt["option_id"]
            current_external_option_ids.add(external_option_id)
            file_name = opt["video_file"]
            video_id = video_id_by_file[file_name]
            label = file_name
            text = opt["text"]
            meta_json = json.dumps(opt.get("meta"), ensure_ascii=False) if opt.get("meta") else None

            cur = conn.execute(
                "SELECT id FROM options WHERE external_option_id = ?",
                (external_option_id,),
            )
            row = cur.fetchone()
            if row:
                option_id = row["id"]
                conn.execute(
                    "UPDATE options SET pool_id = ?, video_id = ?, label = ?, text = ?, meta_json = ? WHERE id = ?",
                    (pool_id_by_name[pool_name], video_id, label, text, meta_json, option_id),
                )
            else:
                option_id = _uuid()
                conn.execute(
                    "INSERT INTO options (id, pool_id, external_option_id, video_id, label, text, meta_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (option_id, pool_id_by_name[pool_name], external_option_id, video_id, label, text, meta_json),
                )
                inserted["options"] += 1

            option_db_id_by_external[external_option_id] = option_id

        if current_external_option_ids:
            current_ids_list = sorted(current_external_option_ids)
            placeholders = ",".join(["?"] * len(current_ids_list))
            cur = conn.execute(
                f"SELECT COUNT(*) AS c FROM options WHERE pool_id = ? AND external_option_id NOT IN ({placeholders})",
                (pool_id, *current_ids_list),
            )
            to_delete = cur.fetchone()["c"]
            if to_delete:
                conn.execute(
                    f"DELETE FROM options WHERE pool_id = ? AND external_option_id NOT IN ({placeholders})",
                    (pool_id, *current_ids_list),
                )
                deleted["orphan_options"] += int(to_delete)

        for q in pool.get("questions", []):
            external_question_id = q["question_id"]
            stem = q["stem"]
            correct_external = q["correct_option_id"]
            correct_option_id = option_db_id_by_external.get(correct_external)
            if not correct_option_id:
                raise ValueError(
                    f"Question {external_question_id} references missing correct_option_id: {correct_external}"
                )

            cur = conn.execute(
                "SELECT id FROM questions WHERE external_question_id = ?",
                (external_question_id,),
            )
            row = cur.fetchone()
            if row:
                question_id = row["id"]
                conn.execute(
                    "UPDATE questions SET pool_id = ?, stem = ?, correct_option_id = ? WHERE id = ?",
                    (pool_id_by_name[pool_name], stem, correct_option_id, question_id),
                )
            else:
                question_id = _uuid()
                conn.execute(
                    "INSERT INTO questions (id, pool_id, external_question_id, stem, correct_option_id) VALUES (?, ?, ?, ?, ?)",
                    (question_id, pool_id_by_name[pool_name], external_question_id, stem, correct_option_id),
                )
                inserted["questions"] += 1

    cur = conn.execute(
        "SELECT COUNT(*) AS c FROM videos WHERE id NOT IN (SELECT DISTINCT video_id FROM options)"
    )
    to_delete_videos = cur.fetchone()["c"]
    if to_delete_videos:
        conn.execute(
            "DELETE FROM videos WHERE id NOT IN (SELECT DISTINCT video_id FROM options)"
        )
        deleted["orphan_videos"] = int(to_delete_videos)

    conn.commit()
    missing_unique = sorted(set(missing_videos))
    return {"inserted": inserted, "deleted": deleted, "missing_videos": missing_unique}


def main() -> None:
    with open(POOL_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)

    if isinstance(raw, list) and raw and "options" in raw[0] and "category_key" in raw[0]:
        transformed = raw
    else:
        transformed = transform_question_pool(raw)
        with open(POOL_PATH, "w", encoding="utf-8") as f:
            json.dump(transformed, f, ensure_ascii=False, indent=2)
            f.write("\n")

    conn = sqlite3.connect(DB_PATH)
    try:
        ensure_schema(conn)
        result = import_into_sqlite(conn, transformed)
    finally:
        conn.close()

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
