import base64
import hashlib
import hmac
import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt
from pydantic import BaseModel, ConfigDict, Field


ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
ICEARTBENCH_ROOT = os.environ.get('ICEARTBENCH_ROOT', ROOT_DIR)
DATA_DIR = os.environ.get('ICEARTBENCH_DATA_DIR', os.path.join(ICEARTBENCH_ROOT, 'data'))
DATA_DB_PATH = os.path.join(DATA_DIR, 'local_dev.db')
MEDIA_DIR = os.environ.get('ICEARTBENCH_MEDIA_DIR', os.path.join(ICEARTBENCH_ROOT, 'media'))
REVIEW_DYNAMIC_DIR = os.path.join(DATA_DIR, 'review_pools', 'dynamic')

JWT_SECRET = os.environ.get('APP_JWT_SECRET', 'dev-secret-change-me')
JWT_ALGORITHM = 'HS256'
JWT_EXPIRES_SECONDS = int(os.environ.get('APP_JWT_EXPIRES_SECONDS', '86400'))


def _utc_ms() -> int:
    return int(time.time() * 1000)


def _uuid() -> str:
    return str(uuid.uuid4())


def _ms_to_iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _hash_password(password: str) -> str:
    iterations = 210_000
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations, dklen=32)
    return 'pbkdf2_sha256$%d$%s$%s' % (
        iterations,
        base64.urlsafe_b64encode(salt).decode('utf-8'),
        base64.urlsafe_b64encode(dk).decode('utf-8'),
    )


def _verify_password(password: str, stored: str) -> bool:
    try:
        alg, iter_s, salt_b64, dk_b64 = stored.split('$', 3)
        if alg != 'pbkdf2_sha256':
            return False
        iterations = int(iter_s)
        salt = base64.urlsafe_b64decode(salt_b64.encode('utf-8'))
        expected = base64.urlsafe_b64decode(dk_b64.encode('utf-8'))
    except Exception:
        return False

    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations, dklen=len(expected))
    return hmac.compare_digest(dk, expected)


def _db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DATA_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON;')
    return conn


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    cur = conn.execute(f'PRAGMA table_info({table_name})')
    return {row['name'] for row in cur.fetchall()}


def _users_has_username_column(conn: sqlite3.Connection) -> bool:
    return 'username' in _table_columns(conn, 'users')


def _insert_user(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    name: str,
    password_hash: str,
    role: str,
    created_at_ms: int,
) -> None:
    if _users_has_username_column(conn):
        conn.execute(
            '''
            INSERT INTO users (
                id, username, name, password_hash, role, is_active, created_at_ms, updated_at_ms, last_login_at_ms
            )
            VALUES (?, ?, ?, ?, ?, 1, ?, ?, NULL)
            ''',
            (user_id, name, name, password_hash, role, created_at_ms, created_at_ms),
        )
    else:
        conn.execute(
            '''
            INSERT INTO users (
                id, name, password_hash, role, is_active, created_at_ms, updated_at_ms, last_login_at_ms
            )
            VALUES (?, ?, ?, ?, 1, ?, ?, NULL)
            ''',
            (user_id, name, password_hash, role, created_at_ms, created_at_ms),
        )


def _ensure_users_schema(conn: sqlite3.Connection) -> None:
    existing = _table_columns(conn, 'users')
    if not existing:
        conn.executescript(
            '''
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('user', 'admin')),
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL,
                last_login_at_ms INTEGER
            );
            '''
        )
        return

    if 'name' not in existing:
        conn.execute('ALTER TABLE users ADD COLUMN name TEXT')
        if 'username' in existing:
            conn.execute("UPDATE users SET name = TRIM(username) WHERE name IS NULL OR name = ''")
        else:
            conn.execute("UPDATE users SET name = id WHERE name IS NULL OR name = ''")

    if 'is_active' not in existing:
        conn.execute('ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1')

    if 'updated_at_ms' not in existing:
        conn.execute('ALTER TABLE users ADD COLUMN updated_at_ms INTEGER')
        conn.execute(
            '''
            UPDATE users
            SET updated_at_ms = COALESCE(created_at_ms, ?)
            WHERE updated_at_ms IS NULL
            ''',
            (_utc_ms(),),
        )

    if 'last_login_at_ms' not in existing:
        conn.execute('ALTER TABLE users ADD COLUMN last_login_at_ms INTEGER')

    if 'created_at_ms' not in existing:
        conn.execute('ALTER TABLE users ADD COLUMN created_at_ms INTEGER')
        conn.execute(
            '''
            UPDATE users
            SET created_at_ms = COALESCE(updated_at_ms, ?)
            WHERE created_at_ms IS NULL
            ''',
            (_utc_ms(),),
        )

    conn.execute("UPDATE users SET name = TRIM(name) WHERE name IS NOT NULL")
    conn.execute("UPDATE users SET is_active = 1 WHERE is_active IS NULL")
    conn.execute(
        '''
        UPDATE users
        SET updated_at_ms = COALESCE(updated_at_ms, created_at_ms, ?)
        WHERE updated_at_ms IS NULL
        ''',
        (_utc_ms(),),
    )
    conn.execute(
        '''
        UPDATE users
        SET created_at_ms = COALESCE(created_at_ms, updated_at_ms, ?)
        WHERE created_at_ms IS NULL
        ''',
        (_utc_ms(),),
    )
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_users_name_unique ON users(name)')


def _ensure_review_schema(conn: sqlite3.Connection) -> None:
    required_columns = {
        'id',
        'file_key',
        'item_key',
        'question_key',
        'video_id',
        'video_path',
        'video_uri',
        'type',
        'dimension',
        'q_category',
        'question',
        'options_json',
        'answer',
        'status',
        'is_delete',
        'is_modified',
        'origin_snapshot_path',
        'dynamic_snapshot_path',
        'created_at_ms',
        'updated_at_ms',
        'modified_at_ms',
        'modified_by',
    }
    existing = _table_columns(conn, 'review_items')
    if existing and not required_columns.issubset(existing):
        conn.executescript(
            '''
            DROP TABLE IF EXISTS review_actions;
            DROP TABLE IF EXISTS review_items;
            '''
        )

    conn.executescript(
        '''
        CREATE TABLE IF NOT EXISTS review_items (
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

        CREATE INDEX IF NOT EXISTS idx_review_items_file_key ON review_items(file_key);
        CREATE INDEX IF NOT EXISTS idx_review_items_type_category ON review_items(type, q_category);
        CREATE INDEX IF NOT EXISTS idx_review_items_type_dimension_category ON review_items(type, dimension, q_category);
        CREATE INDEX IF NOT EXISTS idx_review_items_video_id ON review_items(video_id);

        CREATE TABLE IF NOT EXISTS review_actions (
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


def _ensure_runtime_schema() -> None:
    os.makedirs(REVIEW_DYNAMIC_DIR, exist_ok=True)
    conn = _db_connect()
    try:
        _ensure_users_schema(conn)
        _ensure_review_schema(conn)

        cur = conn.execute('SELECT COUNT(*) AS c FROM users')
        if cur.fetchone()['c'] == 0:
            now = _utc_ms()
            _insert_user(
                conn,
                user_id=_uuid(),
                name='admin',
                password_hash=_hash_password('admin123'),
                role='admin',
                created_at_ms=now,
            )
        else:
            if _users_has_username_column(conn):
                cur = conn.execute(
                    "SELECT id, name, username, password_hash FROM users WHERE name = 'admin' OR username = 'admin'"
                )
            else:
                cur = conn.execute("SELECT id, name, password_hash FROM users WHERE name = 'admin'")
            for row in cur.fetchall():
                stored = row['password_hash'] or ''
                if isinstance(stored, str) and stored.startswith('pbkdf2_sha256$'):
                    continue
                admin_name = row['name'] if row['name'] else row['username']
                conn.execute(
                    'UPDATE users SET password_hash = ?, updated_at_ms = ?, name = ? WHERE id = ?',
                    (_hash_password('admin123'), _utc_ms(), admin_name, row['id']),
                )
            if _users_has_username_column(conn):
                admin_exists = conn.execute("SELECT 1 FROM users WHERE name = 'admin' OR username = 'admin'").fetchone()
            else:
                admin_exists = conn.execute("SELECT 1 FROM users WHERE name = 'admin'").fetchone()
            if not admin_exists:
                now = _utc_ms()
                _insert_user(
                    conn,
                    user_id=_uuid(),
                    name='admin',
                    password_hash=_hash_password('admin123'),
                    role='admin',
                    created_at_ms=now,
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
    name: str
    role: Literal['user', 'admin']


def _create_access_token(user: CurrentUser) -> str:
    now_s = int(time.time())
    payload = {
        'sub': user.id,
        'name': user.name,
        'role': user.role,
        'iat': now_s,
        'exp': now_s + JWT_EXPIRES_SECONDS,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])


def get_current_user(request: Request, db: sqlite3.Connection = Depends(get_db)) -> CurrentUser:
    auth = request.headers.get('authorization') or ''
    if not auth.lower().startswith('bearer '):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Missing bearer token')
    token = auth.split(' ', 1)[1].strip()
    try:
        payload = _decode_token(token)
        user_id = payload.get('sub')
        if not isinstance(user_id, str) or not user_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid token')
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid token')

    cur = db.execute('SELECT id, name, role, is_active FROM users WHERE id = ?', (user_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='User not found')
    if not row['is_active']:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='User is inactive')
    return CurrentUser(id=row['id'], name=row['name'], role=row['role'])


class RegisterRequest(BaseModel):
    name: str = Field(min_length=2, max_length=30)
    password: str = Field(min_length=6, max_length=100)


class RegisterResponse(BaseModel):
    id: str
    name: str
    role: Literal['user', 'admin']


class LoginRequest(BaseModel):
    name: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: Literal['bearer'] = 'bearer'
    role: Literal['user', 'admin']
    name: str


class MeResponse(BaseModel):
    id: str
    name: str
    role: Literal['user', 'admin']


class ReviewItemOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    file_key: str
    item_key: str
    question_key: str
    video_id: str
    video_path: str
    video_uri: str
    type: str
    dimension: str
    q_category: str
    question: str
    options: list[str]
    answer: str
    status: Literal['pending', 'passed', 'modified', 'deleted']
    is_delete: bool
    is_modified: bool
    modified_at: str | None = Field(default=None, alias='_modifiedAt')
    modified_by: str | None = Field(default=None, alias='_modified_by')


class ReviewItemsResponse(BaseModel):
    items: list[ReviewItemOut]


class ModifyReviewRequest(BaseModel):
    answer: str = Field(min_length=1)


def _row_to_review_item(row: sqlite3.Row) -> ReviewItemOut:
    return ReviewItemOut(
        id=row['id'],
        file_key=row['file_key'],
        item_key=row['item_key'],
        question_key=row['question_key'],
        video_id=row['video_id'],
        video_path=row['video_path'],
        video_uri=row['video_uri'],
        type=row['type'],
        dimension=row['dimension'],
        q_category=row['q_category'],
        question=row['question'],
        options=json.loads(row['options_json']),
        answer=row['answer'],
        status=row['status'],
        is_delete=bool(row['is_delete']),
        is_modified=bool(row['is_modified']),
        modified_at=_ms_to_iso(row['modified_at_ms']),
        modified_by=row['modified_by'],
    )


def _load_review_item(db: sqlite3.Connection, item_id: str) -> sqlite3.Row:
    cur = db.execute('SELECT * FROM review_items WHERE id = ?', (item_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail='Review item not found')
    return row


def _write_dynamic_group(db: sqlite3.Connection, file_key: str) -> None:
    cur = db.execute('SELECT * FROM review_items WHERE file_key = ? ORDER BY question_key', (file_key,))
    rows = cur.fetchall()
    if not rows:
        return
    first = rows[0]
    payload = {
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
    with open(first['dynamic_snapshot_path'], 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write('\n')


def _record_action(
    db: sqlite3.Connection,
    *,
    review_item_id: str,
    action_type: Literal['PASS', 'MODIFY', 'DELETE'],
    before_answer: str,
    after_answer: str,
    user: CurrentUser,
) -> None:
    db.execute(
        '''
        INSERT INTO review_actions (
            id, review_item_id, action_type, before_answer, after_answer, operator_id, operator_name, created_at_ms
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (_uuid(), review_item_id, action_type, before_answer, after_answer, user.id, user.name, _utc_ms()),
    )


app = FastAPI(title='Review QA API')

app.add_middleware(
    CORSMiddleware,
    allow_origins=['http://localhost:5173', 'http://127.0.0.1:5173'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

if os.path.isdir(MEDIA_DIR):
    app.mount('/media', StaticFiles(directory=MEDIA_DIR), name='media')


@app.on_event('startup')
def _startup() -> None:
    _ensure_runtime_schema()


@app.exception_handler(HTTPException)
def _http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={'detail': exc.detail})


@app.get('/api/v1/health')
def health() -> dict[str, str]:
    return {'status': 'ok'}


@app.post('/api/v1/auth/register', response_model=RegisterResponse)
def register(payload: RegisterRequest, db: sqlite3.Connection = Depends(get_db)) -> RegisterResponse:
    name = payload.name.strip()
    password = payload.password.strip()
    if len(name) < 2:
        raise HTTPException(status_code=400, detail='Name must be at least 2 characters')
    if len(password) < 6:
        raise HTTPException(status_code=400, detail='Password must be at least 6 characters')

    if _users_has_username_column(db):
        existing = db.execute('SELECT 1 FROM users WHERE name = ? OR username = ?', (name, name)).fetchone()
    else:
        existing = db.execute('SELECT 1 FROM users WHERE name = ?', (name,)).fetchone()
    if existing:
        raise HTTPException(status_code=409, detail='Name already registered')

    now = _utc_ms()
    user_id = _uuid()
    _insert_user(
        db,
        user_id=user_id,
        name=name,
        password_hash=_hash_password(password),
        role='user',
        created_at_ms=now,
    )
    db.commit()
    return RegisterResponse(id=user_id, name=name, role='user')


@app.post('/api/v1/auth/login', response_model=LoginResponse)
def login(payload: LoginRequest, db: sqlite3.Connection = Depends(get_db)) -> LoginResponse:
    login_name = payload.name.strip()
    if _users_has_username_column(db):
        cur = db.execute(
            'SELECT id, name, username, password_hash, role, is_active FROM users WHERE name = ? OR username = ?',
            (login_name, login_name),
        )
    else:
        cur = db.execute(
            'SELECT id, name, password_hash, role, is_active FROM users WHERE name = ?',
            (login_name,),
        )
    row = cur.fetchone()
    if not row or not _verify_password(payload.password, row['password_hash']):
        raise HTTPException(status_code=401, detail='Invalid credentials')
    if not row['is_active']:
        raise HTTPException(status_code=403, detail='User is inactive')

    now = _utc_ms()
    db.execute('UPDATE users SET last_login_at_ms = ?, updated_at_ms = ? WHERE id = ?', (now, now, row['id']))
    db.commit()
    resolved_name = row['name'] if row['name'] else (row['username'] if 'username' in row.keys() else login_name)
    user = CurrentUser(id=row['id'], name=resolved_name, role=row['role'])
    return LoginResponse(access_token=_create_access_token(user), role=user.role, name=user.name)


@app.get('/api/v1/auth/me', response_model=MeResponse)
def me(current_user: CurrentUser = Depends(get_current_user)) -> MeResponse:
    return MeResponse(id=current_user.id, name=current_user.name, role=current_user.role)


@app.get('/api/v1/review-items', response_model=ReviewItemsResponse)
def review_items(
    dimension: str | None = None,
    type: str | None = None,
    q_category: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
) -> ReviewItemsResponse:
    del current_user
    clauses: list[str] = []
    params: list[Any] = []
    if dimension:
        clauses.append('dimension = ?')
        params.append(dimension)
    if type:
        clauses.append('type = ?')
        params.append(type)
    if q_category:
        clauses.append('q_category = ?')
        params.append(q_category)
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ''

    cur = db.execute(
        f'''
        SELECT *
        FROM review_items
        {where_sql}
        ORDER BY dimension, type, q_category, video_id, question_key
        ''',
        tuple(params),
    )
    return ReviewItemsResponse(items=[_row_to_review_item(row) for row in cur.fetchall()])


@app.get('/api/v1/review-items/{item_id}', response_model=ReviewItemOut)
def review_item_detail(
    item_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
) -> ReviewItemOut:
    del current_user
    return _row_to_review_item(_load_review_item(db, item_id))


@app.post('/api/v1/review-items/{item_id}/pass', response_model=ReviewItemOut)
def review_item_pass(
    item_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
) -> ReviewItemOut:
    row = _load_review_item(db, item_id)
    before_answer = row['answer']
    now = _utc_ms()
    db.execute(
        'UPDATE review_items SET status = ?, updated_at_ms = ? WHERE id = ?',
        ('passed', now, item_id),
    )
    _record_action(
        db,
        review_item_id=item_id,
        action_type='PASS',
        before_answer=before_answer,
        after_answer=before_answer,
        user=current_user,
    )
    _write_dynamic_group(db, row['file_key'])
    db.commit()
    return _row_to_review_item(_load_review_item(db, item_id))


@app.post('/api/v1/review-items/{item_id}/delete', response_model=ReviewItemOut)
def review_item_delete(
    item_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
) -> ReviewItemOut:
    row = _load_review_item(db, item_id)
    before_answer = row['answer']
    now = _utc_ms()
    db.execute(
        '''
        UPDATE review_items
        SET status = ?, is_delete = 1, is_modified = 1, modified_at_ms = ?, modified_by = ?, updated_at_ms = ?
        WHERE id = ?
        ''',
        ('deleted', now, current_user.name, now, item_id),
    )
    _record_action(
        db,
        review_item_id=item_id,
        action_type='DELETE',
        before_answer=before_answer,
        after_answer=before_answer,
        user=current_user,
    )
    _write_dynamic_group(db, row['file_key'])
    db.commit()
    return _row_to_review_item(_load_review_item(db, item_id))


@app.post('/api/v1/review-items/{item_id}/modify', response_model=ReviewItemOut)
def review_item_modify(
    item_id: str,
    payload: ModifyReviewRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
) -> ReviewItemOut:
    row = _load_review_item(db, item_id)
    before_answer = row['answer']
    new_answer = payload.answer.strip()
    if not new_answer:
        raise HTTPException(status_code=400, detail='answer cannot be empty')

    now = _utc_ms()
    db.execute(
        '''
        UPDATE review_items
        SET answer = ?, status = ?, is_modified = 1, modified_at_ms = ?, modified_by = ?, updated_at_ms = ?
        WHERE id = ?
        ''',
        (new_answer, 'modified', now, current_user.name, now, item_id),
    )
    _record_action(
        db,
        review_item_id=item_id,
        action_type='MODIFY',
        before_answer=before_answer,
        after_answer=new_answer,
        user=current_user,
    )
    _write_dynamic_group(db, row['file_key'])
    db.commit()
    return _row_to_review_item(_load_review_item(db, item_id))
