# 交互式视频选择题平台（花滑项目）详细实现步骤（按 MVP 规划执行）
  
本文把 [mvp_implementation_plan.md](file:///home/bella/Check/mvp_implementation_plan.md) 拆成可落地的工程步骤清单，目标是先在本地完成「普通用户登录后可答题」闭环（左侧题目/右侧视频/点击即记录），再平滑迁移到学校服务器。
  
当前你本地已准备好：
  
- 题池：`data/question_pool.json`
- 本地数据库（SQLite）：`data/local_dev.db`
- 视频：`media/clips/j/*.mp4`、`media/clips/c/*.mp4`
- 导入脚本：`setup_local_db.py`（可重复执行更新 DB）
  
---
  
## 0. 目录与约定（先统一）
  
### 0.1 建议目录结构（本地开发）
  
- `frontend/`：React 前端
- `backend/`：FastAPI 后端
- `data/`
  - `local_dev.db`
  - `question_pool.json`
- `media/clips/`
  - `j/`
  - `c/`
  
### 0.2 本地开发约定
  
- 本地先用 SQLite 跑通闭环；后续迁移 PostgreSQL 时保持表结构语义一致
- 视频通过后端或 Nginx 静态目录对外提供（URI 采用 `/media/clips/...`）
- 点击即记录不包含视频时间字段
  
---
  
## 1. 本地数据库准备（已完成但需要固化流程）
  
### 1.1 题池与视频检查
  
1) 确认视频文件放在：
  
- `media/clips/j/j1.mp4 j2.mp4 j3.mp4`
- `media/clips/c/c45.mp4 c46.mp4 c47.mp4`
  
2) 确认题池在：
  
- `data/question_pool.json`（结构应包含 `pool_name/category_key/options/questions`）
  
### 1.2 重新生成/同步数据库（可重复执行）
  
运行一次脚本以确保 DB 与文件结构一致：
  
```bash
python3 setup_local_db.py
```
  
预期：
  
- `data/local_dev.db` 内有 pools/videos/options/questions
- `videos.uri` 形如 `/media/clips/j/j1.mp4`
  
---
  
## 2. 后端（FastAPI）第一阶段：只做最小闭环
  
目标：提供 4 个核心能力：登录、发卷、拉卷、点击即记录。
  
### 2.1 初始化后端工程
  
1) 创建 `backend/` 目录与虚拟环境（建议 `venv`）
2) 安装依赖（最小集合）：FastAPI、uvicorn、PyJWT 或 python-jose、passlib[bcrypt]、pydantic
3) 先不引入 SQLAlchemy，直接用 sqlite3 连接 `data/local_dev.db` 跑通（后续再升级 ORM）
  
### 2.2 静态托管视频
  
实现一个静态路由，把 `media/` 目录挂到 `/media`：
  
- `/media/clips/j/j1.mp4` 能被浏览器直接访问播放
  
### 2.3 鉴权与角色（最小版本）
  
实现：
  
- `POST /api/v1/auth/login`：返回 `{access_token, role}`
- `GET /api/v1/auth/me`：返回 `{id, username, role}`
  
最小数据方式（本地先跑通）：
  
- 用户表可以先写在 sqlite 里，或先做“内置账号”（但不要把明文密码写进仓库）
  
推荐先做 sqlite users 表：
  
- `users(id, username, password_hash, role)`
  
### 2.4 发卷（抽题出题）接口：`POST /api/v1/quiz/start`
  
MVP 强烈建议在后端把“本轮题目与 ABCD”固化（QuizInstance/QuizItems），避免前端随机导致无法复现。
  
步骤：
  
1) 输入：`pool_id`（或 pool_name）+ `question_count`
2) 从 DB 读取该 pool 下的 questions
3) 为每个 question 生成 4 个选项（ABCD）：
  
- 取该 question 对应的 options（用 `meta.question_id` 过滤）
- 其中 1 个为 correct_option
- 另外 3 个从同题 options 中抽（如果同题只有 5 个选项，就从剩余 4 个里抽 3 个）
- 洗牌形成 A/B/C/D，得到 `correct_index`
  
4) 生成 quiz_instance_id、quiz_item_id
5) 返回给前端：
  
- `quiz_instance_id`
- items：`[{quiz_item_id, stem, options:[{option_id,label,text,video_uri}], order_index}]`
  
注意：
  
- 你目前题池每题只有 5 个选项，因此“错误选项”建议限制在同题范围内（不要跨题抽）
  
### 2.5 拉卷接口：`GET /api/v1/quiz/{quiz_instance_id}`
  
用途：刷新页面恢复同一套题目与选项顺序。
  
步骤：
  
- 返回该 quiz_instance 的 items + 用户已作答状态（AnswerState）
  
### 2.6 点击即记录接口：`POST /api/v1/log-interaction`
  
写入两类数据：
  
1) `interaction_logs`：每次点击都写一条
2) `answer_state`：维护“当前选择/是否确认”以便 UI 恢复
  
payload（不含视频时间）：
  
```json
{
  "quiz_item_id": "uuid",
  "action_type": "SELECT",
  "selected_index": 2,
  "selected_option_id": "uuid",
  "client_timestamp_ms": 1730000000000,
  "sequence_number": 18,
  "client_event_id": "uuid"
}
```
  
后端规则：
  
- 先落 `interaction_logs`
- 再更新 `answer_state`
  - SELECT：更新 selected_index
  - CLEAR：selected_index=null，confirmed=false
  - CONFIRM：confirmed=true，confirmed_at=now
  
幂等建议（本地也建议做）：
  
- `client_event_id` 建唯一索引；重复事件直接忽略或返回 ok
  
---
  
## 3. 前端（React）第一阶段：答题页面闭环
  
目标：登录后进入答题页，能播放视频并完成选择/删除/确认，且每次点击立即调用 `/log-interaction`。
  
### 3.1 初始化前端工程
  
1) 创建 `frontend/`（Vite + React）
2) 增加路由：`/login`、`/quiz`
3) Token 存储：localStorage（MVP 可用）
  
### 3.2 页面与组件拆分（对应 MVP 计划）
  
- `QuizLayout`
  - 拉取 quiz_instance
  - 管理当前题号（order_index）
  - 管理 sequenceCounter + requestQueue
- `QuestionArea`
  - `OptionList`（ABCD）
  - `ActionButtons`（CLEAR/CONFIRM）
- `MediaPreview`
  - `<video controls src={video_uri}>`
- `StatusBar`
  - 已确认题号高亮
  
### 3.3 请求队列（保证顺序）
  
实现目标：
  
- UI 乐观更新：点击立刻高亮
- 请求串行：避免并发乱序
- 每次点击生成：`sequence_number` + `client_event_id`
  
关键逻辑（参考 mvp 规划中的伪代码）：
  
- `requestQueue = requestQueue.then(() => api.post(...))`
  
### 3.4 交互细节
  
- SELECT：
  - UI 高亮选项
  - 立刻 enqueue log
- CLEAR：
  - UI 取消选中 + 取消确认
  - 立刻 enqueue log
- CONFIRM：
  - UI 锁定（可禁用选项）
  - 立刻 enqueue log
  
---
  
## 4. 本地联调验收（必须过的最小用例）
  
### 4.1 核心流程
  
1) 登录成功拿到 token
2) `POST /quiz/start` 返回 2 个 pool 的某一套题（先只做单 pool 也行）
3) 打开题目：
  
- 右侧视频可播放
- 选 A/B/C/D UI 立即变化
  
4) 快速连点：
  
- 后端 `interaction_logs` 能记录多条 SELECT
- 序号可回放（按 client_timestamp_ms + sequence_number）
  
5) CLEAR/CONFIRM：
  
- 日志中有 CLEAR/CONFIRM
- `answer_state` 正确更新，刷新页面能恢复状态
  
### 4.2 数据一致性检查
  
- 同一道 quiz_item 的 options 列表固定
- `selected_option_id` 必须属于该 quiz_item 的 option_ids
  
---
  
## 5. 第二阶段（在闭环基础上扩展）
  
### 5.1 管理员导入题池（先脚本后 UI）
  
顺序建议：
  
1) 先把 `setup_local_db.py` 的导入逻辑迁移为后端管理 API（`POST /admin/import-pool`）
2) 最后再补管理 UI
  
### 5.2 从 SQLite 迁移到 PostgreSQL（准备上服务器）
  
做法：
  
- 把 sqlite3 查询替换为 SQLAlchemy Async + PostgreSQL
- 表结构保持一致（pools/videos/options/questions/quiz_instances/quiz_items/answer_state/interaction_logs）
  
---
  
## 6. 上学校服务器部署（最小部署路线）
  
1) Nginx：
  
- 静态托管前端构建产物
- 静态托管视频目录 `media/`（路径与 `/media/...` 一致）
  
2) 后端：
  
- docker + uvicorn/gunicorn
  
3) 数据库：
  
- PostgreSQL（docker 或学校提供）
  
---
  
## 7. 你现在可以立刻开始做的 3 个最小动作
  
1) 建 `backend/`，实现静态视频路由 `/media`（先保证 mp4 能播）
2) 实现 `POST /quiz/start`：从 `data/local_dev.db` 抽题并返回 `video_uri`
3) 建 `frontend/`，做一个页面把题干 + 4 个选项 + video 播放跑通
