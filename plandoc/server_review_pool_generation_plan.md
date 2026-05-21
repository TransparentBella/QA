# 服务器 Review Pool 与接口改造规划

## 1. 输入与目标

当前新的标准输入为：

```text
data/Skating_and_Composition_QA_Normalized.jsonl
```

每行结构已经归一化为：

```json
{
  "video_path": "/data4/mjz/project/sport-artistic/IceArtBench/data/video/stsq/Eva1.webm",
  "video_id": "Eva1",
  "type": "FF",
  "dimension": "Skating_Skills",
  "q_category": "Posture and Body Control",
  "questions": [
    {
      "question_key": "Question-1",
      "question": "...",
      "options": ["干扰项1", "干扰项2", "干扰项3"],
      "answer": "正确答案"
    }
  ]
}
```

目标是在服务器上生成供标注前端使用的动态数据池：

```text
/data4/mjz/project/sport-artistic/IceArtBench/data/review_pools
├── origin
└── dynamic
```

同时更新数据库结构与前后端接口，使当前标注网站能消费 normalized 数据中新增的 `video_path`、`dimension` 等字段。

## 2. 总体数据流

推荐数据流：

```text
Skating_and_Composition_QA_Normalized.jsonl
    -> generate_review_pools_from_normalized.py
    -> data/review_pools/origin/*.json
    -> data/local_dev.db 或 PostgreSQL review_items
    -> data/review_pools/dynamic/*.json
    -> annotation_tool backend API
    -> annotation_tool frontend
```

关键原则：

- `origin/` 是只读原始快照
- `review_items` 是在线共享主数据源
- `dynamic/` 是从数据库导出的当前状态镜像
- 前端播放使用 `video_uri`
- `video_path` 保留为服务器磁盘路径，用于溯源、导入、后续评测

## 3. Review Pool 生成规划

## 3.1 origin 文件粒度

继续沿用当前审核模式规划中的粒度：

```text
一个 origin 文件 = 一个 video_id + 一个 q_category
```

文件名建议：

```text
{safe_video_id}__{safe_q_category}.json
```

例如：

```text
Eva1__Posture_and_Body_Control.json
Eva1__Flow_and_Edge_Glide.json
Chsq23__Originality_Creative_Expression.json
j31__11_Energy_Variety_Contrast.json
```

注意：

- `q_category` 中可能包含空格、`&`、`.` 等字符，文件名必须做 safe 化
- DB 中仍保存原始 `q_category` 文本，不使用 safe 后的文件名反推类别

## 3.2 origin 文件格式

建议新 origin 格式：

```json
{
  "video_path": "/data4/.../IceArtBench/data/video/stsq/Eva1.webm",
  "video_uri": "/media/video/stsq/Eva1.webm",
  "video_id": "Eva1",
  "type": "FF",
  "dimension": "Skating_Skills",
  "q_category": "Posture and Body Control",
  "questions": [
    {
      "question_key": "Question-1",
      "question": "...",
      "options": ["...", "...", "..."],
      "answer": "..."
    }
  ]
}
```

与旧本地 `origin/*.json` 相比新增：

- `video_path`
- `video_uri`
- `dimension`

`source_video_path` 不进入 origin 常规字段。它只保留在 normalized JSONL 或报告中用于追溯。

## 3.3 video_uri 生成规则

后端当前通过：

```python
app.mount('/media', StaticFiles(directory=MEDIA_DIR), name='media')
```

对外暴露媒体文件。

服务器建议设置：

```text
MEDIA_DIR=/data4/mjz/project/sport-artistic/IceArtBench/data
```

则：

```text
video_path = /data4/mjz/project/sport-artistic/IceArtBench/data/video/stsq/Eva1.webm
video_uri  = /media/video/stsq/Eva1.webm
```

生成规则：

1. 确认 `video_path` 在 `IceArtBench/data` 内
2. 去掉 `IceArtBench/data` 前缀
3. 前面加 `/media`

如果后端仍将 `MEDIA_DIR` 指向旧的 `media/` 目录，则必须改为环境变量配置，否则服务器视频无法播放。

## 3.4 dynamic 文件格式

`dynamic/*.json` 应包含 origin 顶层元信息和每题审核状态：

```json
{
  "video_path": "/data4/.../IceArtBench/data/video/stsq/Eva1.webm",
  "video_uri": "/media/video/stsq/Eva1.webm",
  "video_id": "Eva1",
  "type": "FF",
  "dimension": "Skating_Skills",
  "q_category": "Posture and Body Control",
  "questions": [
    {
      "question_key": "Question-1",
      "question": "...",
      "options": ["...", "...", "..."],
      "answer": "...",
      "status": "pending",
      "is_delete": false,
      "is_modified": false,
      "_modifiedAt": null,
      "_modified_by": null
    }
  ]
}
```

## 4. 数据库结构更新规划

## 4.1 当前 review_items 字段

当前 `backend/app/main.py` 中 `review_items` 已有：

```text
id
file_key
item_key
question_key
video_id
video_uri
type
q_category
question
options_json
answer
status
is_delete
is_modified
origin_snapshot_path
dynamic_snapshot_path
created_at_ms
updated_at_ms
modified_at_ms
modified_by
```

## 4.2 必须新增字段

为了完整承接 normalized JSONL，`review_items` 至少新增：

```text
video_path TEXT NOT NULL
dimension TEXT NOT NULL
```

原因：

- `video_path` 是服务器真实视频位置，评测与数据回溯需要
- `dimension` 是 normalized 顶层标准字段，后续筛选、统计、评测都需要
- 当前前端播放仍使用 `video_uri`，但 `video_uri` 可由 `video_path` 派生，二者职责不同

## 4.3 建议新增字段

建议新增但不阻塞第一阶段：

```text
source_record_index INTEGER
source_jsonl_path TEXT
```

用途：

- 回溯 normalized JSONL 中的原始行号
- 发现标注问题时能快速定位源文件

如果希望第一阶段保持简单，可以先不加这两个字段，只在导入 summary 或报告中保留。

## 4.4 更新后的 review_items 建议字段

第一阶段推荐字段：

```text
id TEXT PRIMARY KEY
file_key TEXT NOT NULL
item_key TEXT NOT NULL UNIQUE
question_key TEXT NOT NULL
video_id TEXT NOT NULL
video_path TEXT NOT NULL
video_uri TEXT NOT NULL
type TEXT NOT NULL
dimension TEXT NOT NULL
q_category TEXT NOT NULL
question TEXT NOT NULL
options_json TEXT NOT NULL
answer TEXT NOT NULL
status TEXT NOT NULL CHECK (status IN ('pending', 'passed', 'modified', 'deleted'))
is_delete INTEGER NOT NULL DEFAULT 0
is_modified INTEGER NOT NULL DEFAULT 0
origin_snapshot_path TEXT NOT NULL
dynamic_snapshot_path TEXT NOT NULL
created_at_ms INTEGER NOT NULL
updated_at_ms INTEGER NOT NULL
modified_at_ms INTEGER
modified_by TEXT
```

索引建议：

```sql
CREATE INDEX idx_review_items_file_key ON review_items(file_key);
CREATE INDEX idx_review_items_type_dimension_category ON review_items(type, dimension, q_category);
CREATE INDEX idx_review_items_video_id ON review_items(video_id);
```

## 4.5 review_actions 是否更新

`review_actions` 当前足够支持审核行为：

```text
id
review_item_id
action_type
before_answer
after_answer
operator_id
operator_name
created_at_ms
```

第一阶段不需要改。

可选增强：

```text
before_status
after_status
client_timestamp_ms
```

这些用于更细粒度回放，但不是生成 dynamic 池的必要条件。

## 5. 导入脚本规划

新增脚本：

```text
pipeline/generate_review_pools_from_normalized.py
```

输入：

```text
data/Skating_and_Composition_QA_Normalized.jsonl
```

输出：

```text
data/review_pools/origin/*.json
data/review_pools/dynamic/*.json
data/local_dev.db
```

核心步骤：

1. 逐行读取 normalized JSONL
2. 为每行生成 `file_key`
3. 为每个 question 生成 `item_key`
4. 生成 `video_uri`
5. 写入 `origin/*.json`
6. 初始化或迁移 `review_items` 表
7. 插入每个 question 对应的 `review_items`
8. 写入 `review_actions` 的 `INIT`
9. 从 DB 导出 `dynamic/*.json`

## 6. 当前后端不一致与改造方案

## 6.1 路径常量不一致

当前后端：

```python
ROOT_DIR = annotation_tool 项目根或本地仓库根
DATA_DB_PATH = ROOT_DIR/data/local_dev.db
MEDIA_DIR = ROOT_DIR/media
REVIEW_DYNAMIC_DIR = ROOT_DIR/data/review_pools/dynamic
```

服务器真实结构：

```text
IceArtBench/
├── annotation_tool
└── data
    ├── local_dev.db
    ├── review_pools
    └── video
```

改造方案：

```text
ICEARTBENCH_ROOT=/data4/mjz/project/sport-artistic/IceArtBench
ICEARTBENCH_DATA_DIR=/data4/mjz/project/sport-artistic/IceArtBench/data
ICEARTBENCH_MEDIA_DIR=/data4/mjz/project/sport-artistic/IceArtBench/data
```

后端使用：

```python
DATA_DB_PATH = ICEARTBENCH_DATA_DIR/local_dev.db
MEDIA_DIR = ICEARTBENCH_MEDIA_DIR
REVIEW_DYNAMIC_DIR = ICEARTBENCH_DATA_DIR/review_pools/dynamic
```

## 6.2 schema 检查逻辑不一致

当前 `_ensure_review_schema()` 的 `required_columns` 没有：

```text
video_path
dimension
```

需要更新：

- `required_columns` 增加 `video_path`、`dimension`
- `CREATE TABLE review_items` 增加这两个字段
- 如果旧表缺字段，当前逻辑会 drop 重建；服务器部署前要明确是否允许清空旧审核状态

如果服务器已经有真实审核结果，不应直接 drop，应写迁移 SQL：

```sql
ALTER TABLE review_items ADD COLUMN video_path TEXT;
ALTER TABLE review_items ADD COLUMN dimension TEXT;
```

然后用 normalized 数据回填。

## 6.3 API response 不一致

当前 `ReviewItemOut` 没有：

```text
video_path
dimension
```

需要更新：

```python
class ReviewItemOut(BaseModel):
    video_path: str
    video_uri: str
    dimension: str
```

`_row_to_review_item()` 需要补：

```python
video_path=row['video_path']
dimension=row['dimension']
```

`GET /api/v1/review-items` 可以新增筛选参数：

```text
dimension: str | None = None
```

SQL WHERE 增加：

```sql
dimension = ?
```

排序建议改为：

```sql
ORDER BY dimension, type, q_category, video_id, question_key
```

## 6.4 dynamic 导出不一致

当前 `_write_dynamic_group()` 只写：

```text
video_id
type
q_category
questions
```

需要补：

```text
video_path
video_uri
dimension
```

否则 dynamic 池不能完整表达 normalized 数据。

## 7. 当前前端不一致与改造方案

## 7.1 TypeScript 类型不一致

当前 `frontend/src/App.tsx` 的 `ReviewItem` 没有：

```text
video_path
dimension
```

需要更新：

```ts
type ReviewItem = {
  video_path: string
  video_uri: string
  dimension: string
}
```

保留 `video_uri` 用于 `<video src={currentItem.video_uri}>`。

## 7.2 筛选维度不一致

当前前端只有：

```text
selectedType
selectedCategory
```

normalized 数据新增 `dimension` 后，建议前端增加：

```text
selectedDimension
```

筛选顺序建议：

```text
dimension -> type -> q_category
```

原因：

- `dimension` 是最高层任务维度
- `q_category` 是具体审核类别
- `type` 是选手/项目类型，适合保留为辅助筛选

## 7.3 展示字段调整

前端题目卡片或信息栏建议展示：

```text
dimension
q_category
type
video_id
```

不建议默认展示 `video_path`，因为它是服务器绝对路径，对标注人员没有必要。

如需调试，可在管理员模式或 hover tooltip 中显示。

## 8. 前后端接口版本建议

为了减少破坏性，保持原接口路径不变：

```text
GET /api/v1/review-items
GET /api/v1/review-items/{item_id}
POST /api/v1/review-items/{item_id}/pass
POST /api/v1/review-items/{item_id}/delete
POST /api/v1/review-items/{item_id}/modify
```

只扩展 response 字段：

```json
{
  "video_path": "...",
  "video_uri": "...",
  "dimension": "Skating_Skills"
}
```

这样旧前端即使忽略新字段也能继续运行，新前端可以逐步启用 `dimension` 筛选。

## 9. 实施顺序

推荐按以下顺序实施：

1. 新增 `generate_review_pools_from_normalized.py`
2. 生成服务器 `origin/`、`dynamic/` 和 DB
3. 后端改路径环境变量
4. 后端 `review_items` schema 增加 `video_path/dimension`
5. 后端 API response 增加 `video_path/dimension`
6. 后端 `_write_dynamic_group()` 增加顶层元信息
7. 前端 `ReviewItem` 类型增加 `video_path/dimension`
8. 前端增加 `dimension` 筛选
9. 服务器联调视频播放 `/media/video/...`
10. 抽查 dynamic 文件与 API 返回一致

## 10. 验收标准

数据池验收：

- `origin/*.json` 文件数等于 normalized 中唯一 `(video_id, q_category)` 数
- `dynamic/*.json` 文件数与 `origin/*.json` 一致
- `review_items` 行数等于 normalized 中 question 总数
- 每条 `review_items.video_path` 非空
- 每条 `review_items.video_uri` 可由浏览器访问
- 每条 `review_items.dimension` 非空

接口验收：

- `GET /api/v1/review-items` 返回 `video_path/video_uri/dimension`
- `dimension/type/q_category` 筛选可用
- `pass/delete/modify` 后 DB 状态更新
- `pass/delete/modify` 后对应 `dynamic/*.json` 同步更新

前端验收：

- 视频可正常播放
- 题目列表可按 `dimension` 筛选
- 原有 `通过/删除/修改` 流程不受影响
- 修改后刷新页面仍能看到最新状态

## 11. 风险点

### 11.1 旧表 drop 风险

当前 `_ensure_review_schema()` 在字段不满足时会 drop `review_items`。  
如果服务器已有人工审核结果，不能直接使用这个逻辑。

处理建议：

- 首次导入可 drop 重建
- 已有审核结果后必须改成迁移 SQL

### 11.2 source_video_path 是否入库

normalized 中部分记录带 `source_video_path`，表示路径经过上下文消歧。  
第一阶段不建议入 `review_items`，因为它不是业务字段。

如需审计，保留：

```text
pipeline/reports/video_path_context_resolved.csv
```

### 11.3 q_category 文件名 safe 化

`q_category` 不能直接作为文件名使用。  
必须在文件名中 safe 化，但 DB 和 JSON 内容中保存原始文本。



已完成两件事。

  新增脚本：pipeline/generate_review_pools_from_normalized.py

  它会从：

  data/Skating_and_Composition_QA_Normalized.jsonl

  生成：

  data/review_pools/origin/*.json
  data/review_pools/dynamic/*.json
  data/local_dev.db

  并写入新版 review_items 字段：

  video_path
  video_uri
  dimension

  我本地用 /tmp 输出测试过：

  inserted_review_items: 3360
  review_actions: 3360
  origin_files_written: 671
  dynamic_files_written: 671
  duplicate_item_keys_resolved: 5

  video_uri 生成示例：

  /data4/mjz/project/sport-artistic/IceArtBench/data/video/Chsq/Chsq02.mp4
  -> /media/video/Chsq/Chsq02.mp4

  也整理了 .gitignore，现在会忽略 DB、normalized/raw jsonl、review pool 生成物、reports、media、node_modules 等运行产
  物，但不会忽略 pipeline/*.py 脚本。

  上传到服务器
  把这两个文件放到服务器对应位置：

  scp /home/bella/Check/pipeline/generate_review_pools_from_normalized.py \
    root@mars_hjx_2228:/data4/mjz/project/sport-artistic/IceArtBench/pipeline/

  scp /home/bella/Check/data/Skating_and_Composition_QA_Normalized.jsonl \
    root@mars_hjx_2228:/data4/mjz/project/sport-artistic/IceArtBench/data/

  然后在服务器运行：

  cd /data4/mjz/project/sport-artistic/IceArtBench

  python3 pipeline/generate_review_pools_from_normalized.py --clean-output

  注意：这个脚本会重建 review_items/review_actions 表；--clean-output 会清空并重建 data/review_pools/origin 和 data/
  review_pools/dynamic。适合正式标注开始前初始化，不要在已有人工审核结果后直接跑。