import base64
import hashlib
import hmac
import json
import os
import random
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt
from pydantic import BaseModel, Field


ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DATA_DB_PATH = os.path.join(ROOT_DIR, "data", "local_dev.db")
MEDIA_DIR = os.path.join(ROOT_DIR, "media")

JWT_SECRET = os.environ.get("APP_JWT_SECRET", "dev-secret-change-me")
JWT_ALGORITHM = "HS256"
JWT_EXPIRES_SECONDS = int(os.environ.get("APP_JWT_EXPIRES_SECONDS", "86400"))


def _utc_ms() -> int:
    return int(time.time() * 1000)


def _uuid() -> str:
    return str(uuid.uuid4())


def _hash_password(password: str) -> str:
    iterations = 210_000
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, dklen=32)
    return "pbkdf2_sha256$%d$%s$%s" % (
        iterations,
        base64.urlsafe_b64encode(salt).decode("utf-8"),
        base64.urlsafe_b64encode(dk).decode("utf-8"),
    )


def _verify_password(password: str, stored: str) -> bool:
    try:
        alg, iter_s, salt_b64, dk_b64 = stored.split("$", 3)
        if alg != "pbkdf2_sha256":
            return False
        iterations = int(iter_s)
        salt = base64.urlsafe_b64decode(salt_b64.encode("utf-8"))
        expected = base64.urlsafe_b64decode(dk_b64.encode("utf-8"))
    except Exception:
        return False

    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, dklen=len(expected))
    return hmac.compare_digest(dk, expected)


def _db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DATA_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def _ensure_runtime_schema() -> None:
    conn = _db_connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('user', 'admin')),
                created_at_ms INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS quiz_instances (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                pool_id TEXT NOT NULL,
                seed TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL,
                finished_at_ms INTEGER,
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(pool_id) REFERENCES pools(id)
            );

            CREATE TABLE IF NOT EXISTS quiz_items (
                id TEXT PRIMARY KEY,
                quiz_instance_id TEXT NOT NULL,
                question_id TEXT NOT NULL,
                order_index INTEGER NOT NULL,
                option_ids_json TEXT NOT NULL,
                correct_index INTEGER NOT NULL,
                FOREIGN KEY(quiz_instance_id) REFERENCES quiz_instances(id),
                FOREIGN KEY(question_id) REFERENCES questions(id)
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_quiz_items_unique_order
            ON quiz_items(quiz_instance_id, order_index);

            CREATE TABLE IF NOT EXISTS answer_state (
                quiz_item_id TEXT PRIMARY KEY,
                selected_index INTEGER,
                confirmed INTEGER NOT NULL,
                confirmed_at_ms INTEGER,
                updated_at_ms INTEGER NOT NULL,
                FOREIGN KEY(quiz_item_id) REFERENCES quiz_items(id)
            );

            CREATE TABLE IF NOT EXISTS interaction_logs (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                quiz_item_id TEXT NOT NULL,
                action_type TEXT NOT NULL CHECK (action_type IN ('SELECT', 'CLEAR', 'CONFIRM')),
                selected_index INTEGER,
                selected_option_id TEXT,
                client_timestamp_ms INTEGER NOT NULL,
                sequence_number INTEGER NOT NULL,
                client_event_id TEXT NOT NULL UNIQUE,
                created_at_ms INTEGER NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(quiz_item_id) REFERENCES quiz_items(id),
                FOREIGN KEY(selected_option_id) REFERENCES options(id)
            );

            CREATE INDEX IF NOT EXISTS idx_logs_quiz_item_order
            ON interaction_logs(quiz_item_id, client_timestamp_ms, sequence_number);
            """
        )

        cur = conn.execute("SELECT COUNT(*) AS c FROM users")
        if cur.fetchone()["c"] == 0:
            now = _utc_ms()
            conn.execute(
                "INSERT INTO users (id, username, password_hash, role, created_at_ms) VALUES (?, ?, ?, ?, ?)",
                (_uuid(), "user", _hash_password("user123"), "user", now),
            )
            conn.execute(
                "INSERT INTO users (id, username, password_hash, role, created_at_ms) VALUES (?, ?, ?, ?, ?)",
                (_uuid(), "admin", _hash_password("admin123"), "admin", now),
            )
        else:
            cur = conn.execute("SELECT username, password_hash FROM users WHERE username IN ('user', 'admin')")
            rows = list(cur.fetchall())
            for r in rows:
                stored = r["password_hash"] or ""
                if isinstance(stored, str) and stored.startswith("pbkdf2_sha256$"):
                    continue
                if r["username"] == "user":
                    conn.execute(
                        "UPDATE users SET password_hash = ? WHERE username = 'user'",
                        (_hash_password("user123"),),
                    )
                elif r["username"] == "admin":
                    conn.execute(
                        "UPDATE users SET password_hash = ? WHERE username = 'admin'",
                        (_hash_password("admin123"),),
                    )
        conn.commit()
    finally:
        conn.close()


def get_db() -> sqlite3.Connection:
    conn = _db_connect()
    try:
        yield conn
    finally:
        conn.close()


@dataclass(frozen=True)
class CurrentUser:
    id: str
    username: str
    role: Literal["user", "admin"]


def _create_access_token(user: CurrentUser) -> str:
    now_s = int(time.time())
    payload = {
        "sub": user.id,
        "username": user.username,
        "role": user.role,
        "iat": now_s,
        "exp": now_s + JWT_EXPIRES_SECONDS,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])


def get_current_user(request: Request, db: sqlite3.Connection = Depends(get_db)) -> CurrentUser:
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    token = auth.split(" ", 1)[1].strip()
    try:
        payload = _decode_token(token)
        user_id = payload.get("sub")
        if not isinstance(user_id, str) or not user_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    cur = db.execute("SELECT id, username, role FROM users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return CurrentUser(id=row["id"], username=row["username"], role=row["role"])


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    role: Literal["user", "admin"]


class MeResponse(BaseModel):
    id: str
    username: str
    role: Literal["user", "admin"]


class QuizStartRequest(BaseModel):
    pool_name: str = Field(min_length=1)
    question_count: int = Field(default=2, ge=1, le=50)


class QuizOptionOut(BaseModel):
    option_id: str
    label: str
    text: str


class QuizItemOut(BaseModel):
    quiz_item_id: str
    stem: str
    video_uri: str
    options: list[QuizOptionOut]
    order_index: int
    selected_index: int | None = None
    confirmed: bool = False


class QuizStartResponse(BaseModel):
    quiz_instance_id: str
    items: list[QuizItemOut]


ActionType = Literal["SELECT", "CLEAR", "CONFIRM"]


class LogInteractionRequest(BaseModel):
    quiz_item_id: str
    action_type: ActionType
    selected_index: int | None = Field(default=None, ge=0, le=3)
    selected_option_id: str | None = None
    client_timestamp_ms: int
    sequence_number: int
    client_event_id: str


class LogInteractionResponse(BaseModel):
    status: Literal["ok"] = "ok"
    recorded_seq: int


def _row_to_option_out(row: sqlite3.Row) -> QuizOptionOut:
    return QuizOptionOut(
        option_id=row["option_id"],
        label=row["label"],
        text=row["text"],
    )


def _get_pool_by_name(db: sqlite3.Connection, pool_name: str) -> sqlite3.Row:
    cur = db.execute("SELECT id, pool_name, category_key FROM pools WHERE pool_name = ?", (pool_name,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Pool not found: {pool_name}")
    return row


def _load_pool_questions(db: sqlite3.Connection, pool_id: str) -> list[sqlite3.Row]:
    cur = db.execute(
        """
        SELECT q.id AS question_db_id,
               q.external_question_id AS question_id,
               q.stem AS stem,
               q.correct_option_id AS correct_option_id
        FROM questions q
        WHERE q.pool_id = ?
        ORDER BY q.external_question_id
        """,
        (pool_id,),
    )
    return list(cur.fetchall())


def _load_pool_options(db: sqlite3.Connection, pool_id: str) -> list[sqlite3.Row]:
    cur = db.execute(
        """
        SELECT o.id AS option_id,
               o.external_option_id AS external_option_id,
               o.label AS label,
               o.text AS text,
               o.meta_json AS meta_json,
               v.uri AS video_uri
        FROM options o
        JOIN videos v ON v.id = o.video_id
        WHERE o.pool_id = ?
        """,
        (pool_id,),
    )
    return list(cur.fetchall())


def _group_options_by_external_question_id(options: list[sqlite3.Row]) -> dict[str, list[sqlite3.Row]]:
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in options:
        meta_json = row["meta_json"]
        if not meta_json:
            continue
        try:
            meta = json.loads(meta_json)
        except json.JSONDecodeError:
            continue
        qid = meta.get("question_id")
        if not isinstance(qid, str) or not qid:
            continue
        grouped.setdefault(qid, []).append(row)
    return grouped


def _create_quiz_instance(
    db: sqlite3.Connection, user: CurrentUser, pool_id: str, question_count: int
) -> tuple[str, list[QuizItemOut]]:
    pool_questions = _load_pool_questions(db, pool_id)
    if not pool_questions:
        raise HTTPException(status_code=400, detail="Pool has no questions")

    count = min(question_count, len(pool_questions))
    selected_questions = pool_questions[:count]

    pool_options = _load_pool_options(db, pool_id)
    options_by_qid = _group_options_by_external_question_id(pool_options)

    quiz_instance_id = _uuid()
    seed = _uuid()
    now = _utc_ms()

    db.execute(
        "INSERT INTO quiz_instances (id, user_id, pool_id, seed, created_at_ms) VALUES (?, ?, ?, ?, ?)",
        (quiz_instance_id, user.id, pool_id, seed, now),
    )

    items_out: list[QuizItemOut] = []

    for idx, q in enumerate(selected_questions):
        external_question_id = q["question_id"]
        question_db_id = q["question_db_id"]
        stem = q["stem"]
        correct_option_id = q["correct_option_id"]

        candidates = options_by_qid.get(external_question_id, [])
        if not candidates:
            raise HTTPException(
                status_code=400,
                detail=f"Question has no options in pool (meta.question_id missing): {external_question_id}",
            )

        correct_row = None
        for opt in candidates:
            if opt["option_id"] == correct_option_id:
                correct_row = opt
                break
        if correct_row is None:
            raise HTTPException(
                status_code=400,
                detail=f"Correct option not found among candidates for {external_question_id}",
            )

        distractors = [o for o in candidates if o["option_id"] != correct_option_id]
        if len(distractors) < 3:
            raise HTTPException(
                status_code=400,
                detail=f"Not enough distractors for {external_question_id} (need 3, have {len(distractors)})",
            )

        rnd = random.Random(f"{seed}:{external_question_id}")
        wrongs = rnd.sample(distractors, 3)
        option_rows = [correct_row, *wrongs]
        rnd.shuffle(option_rows)

        option_ids = [r["option_id"] for r in option_rows]
        correct_index = option_ids.index(correct_option_id)

        quiz_item_id = _uuid()
        db.execute(
            """
            INSERT INTO quiz_items (id, quiz_instance_id, question_id, order_index, option_ids_json, correct_index)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (quiz_item_id, quiz_instance_id, question_db_id, idx, json.dumps(option_ids), correct_index),
        )
        db.execute(
            """
            INSERT INTO answer_state (quiz_item_id, selected_index, confirmed, confirmed_at_ms, updated_at_ms)
            VALUES (?, NULL, 0, NULL, ?)
            """,
            (quiz_item_id, now),
        )

        items_out.append(
            QuizItemOut(
                quiz_item_id=quiz_item_id,
                stem=stem,
                video_uri=correct_row["video_uri"],
                options=[_row_to_option_out(r) for r in option_rows],
                order_index=idx,
                selected_index=None,
                confirmed=False,
            )
        )

    return quiz_instance_id, items_out


def _load_quiz_instance(db: sqlite3.Connection, quiz_instance_id: str, user: CurrentUser) -> QuizStartResponse:
    cur = db.execute(
        "SELECT id, user_id FROM quiz_instances WHERE id = ?",
        (quiz_instance_id,),
    )
    inst = cur.fetchone()
    if not inst:
        raise HTTPException(status_code=404, detail="Quiz instance not found")
    if inst["user_id"] != user.id:
        raise HTTPException(status_code=403, detail="Forbidden")

    cur = db.execute(
        """
        SELECT qi.id AS quiz_item_id,
               qi.order_index AS order_index,
               q.stem AS stem,
               qi.option_ids_json AS option_ids_json,
               qi.correct_index AS correct_index,
               a.selected_index AS selected_index,
               a.confirmed AS confirmed
        FROM quiz_items qi
        JOIN questions q ON q.id = qi.question_id
        JOIN answer_state a ON a.quiz_item_id = qi.id
        WHERE qi.quiz_instance_id = ?
        ORDER BY qi.order_index
        """,
        (quiz_instance_id,),
    )
    rows = list(cur.fetchall())
    if not rows:
        raise HTTPException(status_code=404, detail="Quiz instance has no items")

    all_option_ids: set[str] = set()
    for r in rows:
        option_ids = json.loads(r["option_ids_json"])
        all_option_ids.update(option_ids)

    placeholders = ",".join(["?"] * len(all_option_ids))
    cur = db.execute(
        f"""
        SELECT o.id AS option_id,
               o.label AS label,
               o.text AS text,
               v.uri AS video_uri
        FROM options o
        JOIN videos v ON v.id = o.video_id
        WHERE o.id IN ({placeholders})
        """,
        tuple(all_option_ids),
    )
    option_map = {r["option_id"]: r for r in cur.fetchall()}

    items: list[QuizItemOut] = []
    for r in rows:
        option_ids = json.loads(r["option_ids_json"])
        option_rows = [option_map[oid] for oid in option_ids]
        correct_option_id = option_ids[r["correct_index"]]
        items.append(
            QuizItemOut(
                quiz_item_id=r["quiz_item_id"],
                stem=r["stem"],
                video_uri=option_map[correct_option_id]["video_uri"],
                options=[_row_to_option_out(orow) for orow in option_rows],
                order_index=r["order_index"],
                selected_index=r["selected_index"],
                confirmed=bool(r["confirmed"]),
            )
        )

    return QuizStartResponse(quiz_instance_id=quiz_instance_id, items=items)


def _get_quiz_item_option_ids(db: sqlite3.Connection, quiz_item_id: str) -> list[str]:
    cur = db.execute("SELECT option_ids_json FROM quiz_items WHERE id = ?", (quiz_item_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Quiz item not found")
    return json.loads(row["option_ids_json"])


app = FastAPI(title="Quiz MVP API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if os.path.isdir(MEDIA_DIR):
    app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")


@app.on_event("startup")
def _startup() -> None:
    _ensure_runtime_schema()


@app.exception_handler(HTTPException)
def _http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest, db: sqlite3.Connection = Depends(get_db)) -> LoginResponse:
    cur = db.execute(
        "SELECT id, username, password_hash, role FROM users WHERE username = ?",
        (payload.username,),
    )
    row = cur.fetchone()
    if not row or not _verify_password(payload.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    user = CurrentUser(id=row["id"], username=row["username"], role=row["role"])
    return LoginResponse(access_token=_create_access_token(user), role=user.role)


@app.get("/api/v1/auth/me", response_model=MeResponse)
def me(current_user: CurrentUser = Depends(get_current_user)) -> MeResponse:
    return MeResponse(id=current_user.id, username=current_user.username, role=current_user.role)


@app.post("/api/v1/quiz/start", response_model=QuizStartResponse)
def quiz_start(
    payload: QuizStartRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
) -> QuizStartResponse:
    pool = _get_pool_by_name(db, payload.pool_name)
    quiz_instance_id, items = _create_quiz_instance(db, current_user, pool["id"], payload.question_count)
    db.commit()
    return QuizStartResponse(quiz_instance_id=quiz_instance_id, items=items)


@app.get("/api/v1/quiz/{quiz_instance_id}", response_model=QuizStartResponse)
def quiz_get(
    quiz_instance_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
) -> QuizStartResponse:
    return _load_quiz_instance(db, quiz_instance_id, current_user)


@app.post("/api/v1/log-interaction", response_model=LogInteractionResponse)
def log_interaction(
    payload: LogInteractionRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
) -> LogInteractionResponse:
    if payload.action_type in ("SELECT", "CONFIRM"):
        if payload.selected_index is None or payload.selected_option_id is None:
            raise HTTPException(status_code=400, detail="selected_index and selected_option_id required")
    if payload.action_type == "CLEAR":
        if payload.selected_index is not None or payload.selected_option_id is not None:
            raise HTTPException(status_code=400, detail="CLEAR does not take selected fields")

    option_ids = _get_quiz_item_option_ids(db, payload.quiz_item_id)

    if payload.selected_option_id is not None and payload.selected_option_id not in option_ids:
        raise HTTPException(status_code=400, detail="selected_option_id not in quiz_item options")
    if payload.selected_index is not None:
        if payload.selected_index < 0 or payload.selected_index >= len(option_ids):
            raise HTTPException(status_code=400, detail="selected_index out of range")
        expected_option_id = option_ids[payload.selected_index]
        if payload.selected_option_id is not None and payload.selected_option_id != expected_option_id:
            raise HTTPException(status_code=400, detail="selected_index does not match selected_option_id")

    now = _utc_ms()

    cur = db.execute(
        "SELECT id FROM interaction_logs WHERE client_event_id = ?",
        (payload.client_event_id,),
    )
    if cur.fetchone():
        return LogInteractionResponse(recorded_seq=payload.sequence_number)

    db.execute(
        """
        INSERT INTO interaction_logs (
            id, user_id, quiz_item_id, action_type, selected_index, selected_option_id,
            client_timestamp_ms, sequence_number, client_event_id, created_at_ms
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _uuid(),
            current_user.id,
            payload.quiz_item_id,
            payload.action_type,
            payload.selected_index,
            payload.selected_option_id,
            payload.client_timestamp_ms,
            payload.sequence_number,
            payload.client_event_id,
            now,
        ),
    )

    if payload.action_type == "SELECT":
        db.execute(
            """
            UPDATE answer_state
            SET selected_index = ?, updated_at_ms = ?
            WHERE quiz_item_id = ?
            """,
            (payload.selected_index, now, payload.quiz_item_id),
        )
    elif payload.action_type == "CLEAR":
        db.execute(
            """
            UPDATE answer_state
            SET selected_index = NULL, confirmed = 0, confirmed_at_ms = NULL, updated_at_ms = ?
            WHERE quiz_item_id = ?
            """,
            (now, payload.quiz_item_id),
        )
    elif payload.action_type == "CONFIRM":
        db.execute(
            """
            UPDATE answer_state
            SET selected_index = ?, confirmed = 1, confirmed_at_ms = ?, updated_at_ms = ?
            WHERE quiz_item_id = ?
            """,
            (payload.selected_index, now, now, payload.quiz_item_id),
        )

    db.commit()
    return LogInteractionResponse(recorded_seq=payload.sequence_number)
