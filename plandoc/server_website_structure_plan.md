# IceArtBench 服务器网站结构规划

## 1. 目标

GitHub 仓库只管理标注网站代码、pipeline 脚本和规划文档。仓库名建议为：

```text
QA
```

该仓库下载到服务器后放在：

```text
/data4/mjz/project/sport-artistic/IceArtBench/annotation_tool/QA
```

服务器上的大数据、视频、数据库、review pool 动态产物不进入 Git，而是保留在：

```text
/data4/mjz/project/sport-artistic/IceArtBench/data
```

本地 `/home/bella/Check/data` 已加入 `.gitignore`，不再作为 GitHub 上传内容。

## 2. GitHub 仓库建议结构

建议 `QA` 仓库中保留：

```text
QA/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   └── main.py
│   └── requirements.txt
├── frontend/
│   ├── public/
│   ├── src/
│   ├── index.html
│   ├── package.json
│   ├── package-lock.json
│   ├── tsconfig.json
│   ├── tsconfig.node.json
│   └── vite.config.ts
├── pipeline/
│   ├── normalize_skating_qa.py
│   └── generate_review_pools_from_normalized.py
├── plandoc/
│   ├── qa_normalization_plan.md
│   ├── server_review_pool_generation_plan.md
│   └── server_website_structure_plan.md
├── .gitignore
└── README.md
```

也就是说，当前本地项目整体就是 GitHub 仓库内容，不需要再额外套一层 `annotation_tool/`。

## 3. 服务器最终目录结构

服务器上建议保持：

```text
/data4/mjz/project/sport-artistic/IceArtBench
├── annotation_tool
│   └── QA
│       ├── backend
│       ├── frontend
│       ├── pipeline
│       ├── plandoc
│       └── .gitignore
├── data
│   ├── Skating_and_Composition_QA_Final.jsonl
│   ├── Skating_and_Composition_QA_Normalized.jsonl
│   ├── local_dev.db
│   ├── qa_normalization_summary.json
│   ├── review_pools
│   │   ├── origin
│   │   └── dynamic
│   └── video
│       ├── Chsq
│       ├── Jump
│       ├── face
│       ├── music
│       ├── spatial
│       └── stsq
├── evaluation
└── pipeline
```

说明：

- `annotation_tool/QA/` 是 GitHub 代码目录
- `data/` 是服务器数据目录，不由 Git 管理
- `data/local_dev.db` 是当前 SQLite 主库
- `data/review_pools/origin` 是只读 QA 快照
- `data/review_pools/dynamic` 是从 DB 导出的共享动态池
- `data/video` 是视频源

服务器根目录的 `pipeline/` 可以继续作为数据构建脚本目录。建议从 `annotation_tool/QA/pipeline/*.py` 同步到：

```text
/data4/mjz/project/sport-artistic/IceArtBench/pipeline
```

这样运行路径会更直观：

```bash
cd /data4/mjz/project/sport-artistic/IceArtBench
python3 pipeline/generate_review_pools_from_normalized.py --clean-output
```

如果不想维护两份 pipeline，也可以直接从仓库目录运行：

```bash
cd /data4/mjz/project/sport-artistic/IceArtBench/annotation_tool/QA
python3 pipeline/generate_review_pools_from_normalized.py \
  --input /data4/mjz/project/sport-artistic/IceArtBench/data/Skating_and_Composition_QA_Normalized.jsonl \
  --data-dir /data4/mjz/project/sport-artistic/IceArtBench/data \
  --clean-output
```

## 4. .gitignore 策略

本地和服务器代码仓库中应忽略：

```text
data/
media/
frontend/node_modules/
frontend/dist/
backend/.venv/
pipeline/reports/
*.db
*.sqlite
*.sqlite3
*.log
.env
```

不要忽略：

```text
pipeline/*.py
backend/app/*.py
frontend/src/*
plandoc/*.md
```

原因：

- pipeline 脚本是可复用工程代码，应该进 Git
- data 和 media 是大文件/运行产物，不应进 Git

## 5. 后端路径配置

后端必须从环境变量读取服务器数据目录，避免继续使用本地仓库内的 `data/` 和 `media/`。

建议环境变量：

```bash
export ICEARTBENCH_ROOT=/data4/mjz/project/sport-artistic/IceArtBench
export ICEARTBENCH_DATA_DIR=/data4/mjz/project/sport-artistic/IceArtBench/data
export ICEARTBENCH_MEDIA_DIR=/data4/mjz/project/sport-artistic/IceArtBench/data
```

后端内部应解析为：

```text
DATA_DB_PATH=$ICEARTBENCH_DATA_DIR/local_dev.db
REVIEW_DYNAMIC_DIR=$ICEARTBENCH_DATA_DIR/review_pools/dynamic
MEDIA_DIR=$ICEARTBENCH_MEDIA_DIR
```

这样视频 URL：

```text
/media/video/stsq/Eva1.webm
```

会映射到：

```text
/data4/mjz/project/sport-artistic/IceArtBench/data/video/stsq/Eva1.webm
```

## 6. 数据初始化流程

服务器首次初始化：

```bash
cd /data4/mjz/project/sport-artistic/IceArtBench

python3 pipeline/normalize_skating_qa.py
python3 pipeline/generate_review_pools_from_normalized.py --clean-output
```

预期生成：

```text
data/Skating_and_Composition_QA_Normalized.jsonl
data/local_dev.db
data/review_pools/origin/*.json
data/review_pools/dynamic/*.json
```

当前本地测试的规模为：

```text
records: 671
questions/review_items: 3355
origin files: 671
dynamic files: 671
```

## 7. 网站启动方式

后端：

```bash
cd /data4/mjz/project/sport-artistic/IceArtBench/annotation_tool/QA/backend

export ICEARTBENCH_ROOT=/data4/mjz/project/sport-artistic/IceArtBench
export ICEARTBENCH_DATA_DIR=/data4/mjz/project/sport-artistic/IceArtBench/data
export ICEARTBENCH_MEDIA_DIR=/data4/mjz/project/sport-artistic/IceArtBench/data

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8899
```

前端：

```bash
cd /data4/mjz/project/sport-artistic/IceArtBench/annotation_tool/QA/frontend
npm install
npm run dev -- --host 0.0.0.0 --port 5173
```

生产部署时建议：

- 前端 `npm run build` 后用 Nginx 托管 `dist/`
- 后端用 systemd 或 supervisor 管理 uvicorn
- `/media/` 可继续由 FastAPI 静态服务托管，也可以切到 Nginx 直接托管

## 8. 需要同步的代码改造

为了让服务器网站使用新版数据池，后端还需要改：

1. 路径常量读取环境变量
2. `review_items` schema 增加 `video_path`、`dimension`
3. `ReviewItemOut` 增加 `video_path`、`dimension`
4. `_write_dynamic_group()` 写入 `video_path`、`video_uri`、`dimension`
5. `GET /api/v1/review-items` 支持 `dimension` 筛选

前端还需要改：

1. `ReviewItem` 类型增加 `video_path`、`dimension`
2. 增加 `dimension` 筛选
3. 保持 `<video src={video_uri}>` 不变

## 9. 上传 GitHub 前检查

建议上传前运行：

```bash
git status --short --ignored
```

确认这些内容不会上传：

```text
data/
media/
frontend/node_modules/
frontend/dist/
backend/.venv/
pipeline/reports/
```

确认这些内容会上传：

```text
backend/
frontend/
pipeline/*.py
plandoc/*.md
.gitignore
```
