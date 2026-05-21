# IceArtBench 服务器迁移差异分析与实施步骤

## 1. 说明

本文基于当前本地仓库内容，以及根目录示例文件 `Skating_and_Composition_QA_Final.jsonl` 做差异分析与迁移规划。

当前执行环境中无法直接访问你提到的服务器目录 `/data4/mjz/project/sport-artistic/IceArtBench`，命令返回的是 `No such file or directory`，因此：

- 对“服务器现有 QA” 的判断，依据示例文件 `Skating_and_Composition_QA_Final.jsonl`
- 对“服务器现有视频” 的判断，只能依据示例 QA 中的 `video_path` 书写方式推断
- 真正迁移前，仍需在 mars 上补一次实地核对

## 2. 当前本地网站的实际结构

当前本地项目不是单纯的数据目录，而是一个可运行的审核网站，核心结构如下：

```text
frontend/                  React 前端
backend/                   FastAPI 后端
media/clips/               本地视频
data/review_pools/origin/  原始 QA
data/review_pools/dynamic/ 动态审核结果
data/local_dev.db          本地 SQLite
setup_local_db.py          初始化/导入脚本
```

本地网站实际消费的数据结构不是 `jsonl`，而是按“单个视频 + 单个类别”拆开的 JSON 文件。  
`setup_local_db.py` 会把 QA 导入 `review_items`，并生成：

- `origin/*.json`
- `dynamic/*.json`
- `data/local_dev.db`

本地审核页面直接依赖后端返回的 `video_uri`，例如：

- `/media/clips/j/j1.mp4`
- `/media/clips/c/c45.mp4`

## 3. 当前本地数据的可验证现状

### 3.1 本地 QA 文件数量

- `data/review_pools/origin/`：10 个文件
- 文件名：
  - `j1__Rhythm.json` ~ `j5__Rhythm.json`
  - `c45__Similarity.json` ~ `c49__Similarity.json`

### 3.2 本地视频数量

- `media/clips/`：6 个视频
- 实际只有：
  - `j1.mp4`
  - `j2.mp4`
  - `j3.mp4`
  - `c45.mp4`
  - `c46.mp4`
  - `c47.mp4`

### 3.3 当前本地就存在的数据缺口

本地 QA 与本地视频并不齐：

- QA 中存在，但视频缺失：
  - `j4`
  - `j5`
  - `c48`
  - `c49`

这意味着当前本地网站的数据本身并不满足“完整可迁移”条件。  
迁移前必须先补齐对应视频，或者在迁移脚本中剔除这些缺视频条目。

## 4. 示例服务器 QA 与本地网站结构的主要差异

## 4.1 差异一：存储粒度不同

本地网站：

- 一个文件对应一个 `video_id + q_category`
- 例如 `j1__Rhythm.json`

示例服务器 QA：

- 一个 `jsonl` 文件包含 674 行记录
- 每一行对应一个视频下的一个任务维度

结论：  
服务器示例是“大一统 JSONL”，本地网站是“按视频拆分后的 JSON 文件 + DB + 动态快照”。

## 4.2 差异二：顶层字段命名不一致

本地网站 `origin/*.json` 顶层字段：

```json
{
  "video_id": "j1",
  "type": "MS",
  "q_category": "Rhythm",
  "questions": [...]
}
```

示例服务器 QA 中至少存在两套顶层写法：

### 写法 A

```json
{
  "video_path": "/data4/.../Eva1.mp4",
  "type": "FF",
  "dimension": "Skating_Skills",
  "task": "Posture and Body Control",
  "questions": [...]
}
```

### 写法 B

```json
{
  "video_id": "Chsq55",
  "type": "MF",
  "dimension": "Presentation",
  "q_category": "9. Musical Theme Interpretation",
  "questions": [...]
}
```

统计上：

- 以 `video_path` 开头的记录：255 行
- 以 `video_id` 开头的记录：419 行

结论：  
服务器示例 QA 文件内部本身就不是单一 schema，迁移时必须先做格式归一化。

## 4.3 差异三：问题字段也不一致

本地网站每个问题的结构基本固定：

```json
{
  "question_key": "...",
  "question": "...",
  "options": ["...", "...", "..."],
  "answer": "..."
}
```

示例服务器 QA 至少有两种问题结构：

### 结构 A

```json
{
  "question_key": "Question-1",
  "question": "...",
  "options": ["A...", "B...", "C...", "D..."],
  "answer": "...",
  "correct_option": "A"
}
```

### 结构 B

```json
{
  "question": "...",
  "ground_truth": "...",
  "distractors": ["...", "...", "..."]
}
```

其中：

- 包含 `correct_option` 的记录共 247 行
- 包含 `ground_truth` 的记录至少 3 行

结论：  
本地审核网站要的是统一的 `options + answer` 结构；服务器示例 QA 需要先转换。

## 4.4 差异四：视频定位方式不同

本地网站：

- 使用前端可直接访问的 `video_uri`
- 例如 `/media/clips/j/j1.mp4`

示例服务器 QA：

- 部分记录保存的是绝对磁盘路径 `video_path`
- 例如 `/data4/mjz/project/sport-artistic/data/video/StSq/Eva1.mp4`

结论：  
网站运行时不能直接依赖磁盘绝对路径，必须生成可被前端访问的相对 URI，或者由后端做路径映射。

## 4.5 差异五：目录语义不同

你现在希望服务器最终目录是：

```text
/data4/mjz/project/sport-artistic/IceArtBench
├── annotation_tool
├── data
├── evaluation
└── pipeline
```

而本地仓库当前是：

```text
backend/
frontend/
data/
media/
```

结论：  
这不是简单拷文件，而是一次“目录重组 + 数据格式转换 + 运行方式调整”。

## 5. 建议的目标目录落位

建议迁移到服务器后按下面方式组织：

```text
/data4/mjz/project/sport-artistic/IceArtBench
├── annotation_tool
│   ├── frontend
│   ├── backend
│   └── README.md
├── data
│   ├── qa
│   │   ├── raw
│   │   │   └── Skating_and_Composition_QA_Final.jsonl
│   │   ├── normalized
│   │   └── review_pools
│   │       ├── origin
│   │       └── dynamic
│   ├── video
│   │   ├── raw
│   │   └── clips
│   └── local_dev.db
├── evaluation
│   ├── README.md
│   └── scripts
└── pipeline
    ├── setup_local_db.py
    ├── normalize_server_qa.py
    ├── audit_video_qa_consistency.py
    └── README.md
```

说明：

- `annotation_tool` 里建议同时保留前后端；当前网站不是纯前端
- `data/qa/raw` 保留原始 JSONL，不直接改写
- `data/qa/normalized` 存统一 schema 的中间产物
- `data/qa/review_pools` 存网站直接可用的数据
- `pipeline` 放数据构建与校验脚本

## 6. 迁移实施步骤

## Step 0. 先在 mars 上核对真实现状

在真正迁移前，先在服务器执行以下检查：

1. 确认 `/data4/mjz/project/sport-artistic/IceArtBench` 是否已创建
2. 列出 `data/` 下已有视频目录和 QA 目录
3. 确认 `Skating_and_Composition_QA_Final.jsonl` 的真实存放位置
4. 统计视频总数、QA 总数、缺失文件数
5. 确认是否已有评测脚本与标注前端

这是必须先做的，因为当前环境无法直接访问 mars 路径。

## Step 1. 在服务器创建标准目录

先建立固定目录骨架：

```text
annotation_tool/
data/qa/raw/
data/qa/normalized/
data/qa/review_pools/origin/
data/qa/review_pools/dynamic/
data/video/raw/
data/video/clips/
pipeline/
evaluation/
```

## Step 2. 迁移本地网站代码

目录映射建议：

- `frontend/` -> `annotation_tool/frontend/`
- `backend/` -> `annotation_tool/backend/`

同时修改代码中的路径常量，避免继续写死当前仓库结构：

- `ROOT_DIR`
- `DATA_DB_PATH`
- `MEDIA_DIR`
- `REVIEW_DYNAMIC_DIR`
- `setup_local_db.py` 中的 `DATA_DIR / MEDIA_CLIPS_DIR / REVIEW_POOLS_DIR`

建议把这些路径改为环境变量驱动，至少支持：

- `ICEARTBENCH_ROOT`
- `ICEARTBENCH_DATA_DIR`
- `ICEARTBENCH_MEDIA_DIR`

## Step 3. 保留原始服务器 QA，不直接覆盖

把示例 QA 及服务器上已有 QA 统一放到：

- `data/qa/raw/`

不要直接拿原始 JSONL 替换本地 `review_pools/origin/`，因为 schema 不兼容。

## Step 4. 先做 QA 归一化脚本

在 `pipeline/` 新增一个归一化脚本，例如：

- `normalize_server_qa.py`

它需要完成：

1. 统一顶层字段
   - `video_path` / `video_id` 统一成 `video_id`
   - `task` / `q_category` 统一成 `q_category`
   - 保留 `dimension`

2. 统一问题字段
   - 若是 `options + answer + correct_option` 结构，直接转为本地结构
   - 若是 `ground_truth + distractors` 结构，转为：
     - `options = distractors`
     - `answer = ground_truth`

3. 输出为网站可消费的 group JSON
   - 一个 `video_id + q_category` 输出一个文件

目标输出格式应与本地 `origin/*.json` 一致：

```json
{
  "video_id": "...",
  "type": "...",
  "q_category": "...",
  "questions": [
    {
      "question_key": "...",
      "question": "...",
      "options": ["...", "...", "..."],
      "answer": "..."
    }
  ]
}
```

## Step 5. 做视频与 QA 对账脚本

在 `pipeline/` 新增校验脚本，例如：

- `audit_video_qa_consistency.py`

至少输出三份结果：

1. `qa_without_video.csv`
2. `video_without_qa.csv`
3. `schema_anomalies.csv`

这一步必须在导入数据库前执行。  
原因很直接：当前本地已经有 `j4/j5/c48/c49` 这类“有 QA 无视频”的例子。

## Step 6. 整理视频目录

建议两层保留：

1. 原始视频
   - `data/video/raw/...`
2. 网站实际访问的视频
   - `data/video/clips/...`

如果服务器已有绝对路径视频，例如：

- `/data4/mjz/project/sport-artistic/data/video/StSq/Eva1.mp4`

建议不要让前端直接读这个路径，而是：

1. 复制或软链接到 `IceArtBench/data/video/...`
2. 由后端统一暴露为：
   - `/media/...`

## Step 7. 调整本地导入脚本以适配服务器布局

当前 `setup_local_db.py` 的职责可以保留，但需要改为处理服务器目录：

1. 输入：
   - `data/qa/review_pools/origin/*.json`
   - `data/video/clips/**/*.mp4`

2. 输出：
   - `data/local_dev.db`
   - `data/qa/review_pools/dynamic/*.json`

3. 生成统一的 `video_uri`
   - 例如 `/media/clips/j/j1.mp4`
   - 或 `/media/video/StSq/Eva1.mp4`

关键点是：数据库里存的是网站 URI，不是磁盘绝对路径。

## Step 8. 把构建脚本归档到 pipeline

建议 `pipeline/` 至少包含：

- `setup_local_db.py`
- `normalize_server_qa.py`
- `audit_video_qa_consistency.py`
- `README.md`

其中 `README.md` 写清楚标准执行顺序：

1. 原始 QA 入 `data/qa/raw/`
2. 归一化到 `data/qa/review_pools/origin/`
3. 视频对账
4. 生成 DB 和 `dynamic/`

## Step 9. evaluation 单独整理

当前仓库没有明确的 `evaluation/` 脚本。  
因此迁移时应先创建空目录并补一个说明文件，避免和 `pipeline` 混放。

建议：

- 数据构建、清洗、转换 -> `pipeline/`
- 指标计算、模型评测、结果汇总 -> `evaluation/`

## Step 10. 最终联调验证

迁移完成后至少验证以下内容：

1. `annotation_tool` 可以正常启动
2. 前端能访问视频
3. 后端能读取 `data/local_dev.db`
4. `origin/` 与 `dynamic/` 文件能正常生成
5. 任一审核条目都能打开对应视频
6. 不存在“QA 在库里但视频 404”的情况

## 7. 本次迁移中的关键风险

### 风险 1：服务器 QA 文件内部 schema 不统一

这不是简单字段改名，必须写归一化脚本。

### 风险 2：本地数据本身不完整

至少已有 4 个视频缺失，直接迁移会把坏数据带上服务器。

### 风险 3：当前网站不是纯前端

如果 `annotation_tool` 只放前端，现有审核站无法独立运行。  
必须同步迁移后端，或另行定义服务部署方式。

### 风险 4：绝对路径不可直接作为网站播放地址

`video_path` 只能作为原始定位信息，不能直接给浏览器使用。

## 8. 建议的最小执行顺序

建议按这个顺序做，避免重复返工：

1. 在 mars 核对真实目录与已有文件
2. 创建 `IceArtBench` 标准目录骨架
3. 迁移 `frontend/backend` 到 `annotation_tool/`
4. 迁移原始 QA 到 `data/qa/raw/`
5. 新增 QA 归一化脚本
6. 新增视频-QA 对账脚本
7. 补齐缺失视频或剔除缺失条目
8. 运行 `setup_local_db.py` 生成 DB 与 `dynamic/`
9. 启动标注网站联调

## 9. 当前结论

当前“服务器现有 QA 和视频”与“本地网站结构”的差异，不是单一目录名差异，而是三层差异叠加：

1. 数据 schema 不一致
2. 视频定位方式不一致
3. 工程目录职责划分不一致

因此正确做法不是直接把现有文件拷进 `IceArtBench/data`，而是：

- 保留原始 QA
- 先归一化
- 再对账视频
- 最后导入成网站可用的数据结构

