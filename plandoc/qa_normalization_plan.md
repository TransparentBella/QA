# Skating_and_Composition_QA_Final.jsonl 格式整理规划

## 1. 背景与服务器现状

服务器目录已经确认如下：

```text
/data4/mjz/project/sport-artistic/IceArtBench
├── annotation_tool
├── data
│   ├── Skating_and_Composition_QA_Final.jsonl
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

注意：

- `annotation_tool/` 下没有 `data/`
- QA 和视频当前都在项目根目录的 `data/` 下
- 视频路径细节已经记录在 [path.md](/home/bella/Check/path.md)
- 本规划只处理 `Skating_and_Composition_QA_Final.jsonl` 的字段整理，不直接调整网站部署路径

## 2. 目标

将服务器现有 `Skating_and_Composition_QA_Final.jsonl` 整理为统一、可继续导入标注前端的数据格式。

整理后每一行仍然是一条 JSONL 记录，但 schema 要固定：

```json
{
  "video_path": "/data4/mjz/project/sport-artistic/IceArtBench/data/video/stsq/Eva1.webm",
  "video_id": "Eva1",
  "type": "FF",
  "dimension": "Skating_Skills",
  "q_category": "Posture and Body Control",
  "questions": [
    {
      "question_key": "Posture_and_Body_Control-1",
      "question": "...",
      "options": ["...", "...", "..."],
      "answer": "..."
    }
  ]
}
```

关键要求：

- 顶层必须同时保留 `video_path` 和 `video_id`
- `task` 与 `q_category` 实质等价，最终统一使用 `q_category`
- `video_path` 必须根据 `path.md` 中服务器真实视频目录重新整理
- 问题字段最终向本地标注网站结构靠齐
- `correct_option`、`ground_truth`、`distractors` 等中间字段不保留到最终问题结构

## 3. 顶层字段整理规则

## 3.1 标准顶层字段

最终每条记录保留这些顶层字段：

```text
video_path
video_id
type
dimension
q_category
questions
```

冲突或异常追溯字段：

```text
source_video_path
source_q_category
```

默认不生成这两个字段。只有在发生冲突或异常时才保留来源信息：

- `source_video_path`：仅当原始 `video_path` 与最终匹配到的 `video_path` 不一致，且需要追溯路径修正来源时生成
- `source_q_category`：仅当原始记录同时存在 `task` 和 `q_category`，且二者值不一致时生成

## 3.2 video_id 生成规则

如果原始记录已有 `video_id`：

- 清理首尾空白后直接使用

如果原始记录只有 `video_path`：

- 从文件名去掉扩展名得到 `video_id`
- 例如：
  - `.../Eva1.mp4` -> `Eva1`
  - `.../Chsq56.mp4` -> `Chsq56`
  - `.../spatial10.webm` -> `spatial10`

## 3.3 video_path 整理规则

最终 `video_path` 必须指向当前 IceArtBench 内部真实视频路径，而不是旧路径。

旧路径示例：

```text
/data4/mjz/project/sport-artistic/data/video/StSq/Eva1.mp4
```

服务器当前视频根目录：

```text
/data4/mjz/project/sport-artistic/IceArtBench/data/video
```

整理时根据 `path.md` 建立“文件名 stem -> 实际路径”的索引。

这里不能按编号范围做映射。目录中出现 `Chsq01.mp4` 到 `Chsq56.mp4`、`j1.mp4` 到 `j50.mp4` 只说明这些文件存在，不代表可以把任意编号范围互相映射。真正可靠的匹配依据是同名视频：

```text
video_id == 文件名去扩展名
```

例如：

```text
Chsq55 -> data/video/Chsq/Chsq55.mp4
j4     -> data/video/Jump/j4.mp4
Eva1   -> data/video/stsq/Eva1.webm 或 data/video/music/Eva1.mp4
```

如果同一个 `video_id` 在多个目录中同时存在，不能只靠文件名决定，必须结合原始路径、`dimension` 或人工规则 disambiguate。

匹配策略：

1. 先用 `video_id` 精确匹配文件名去扩展名
2. 如果只有一个同名候选路径，直接使用该路径
3. 如果有多个同名候选路径，例如 `Eva1` 同时存在于 `music` 和 `stsq`，结合原始 `video_path` 的目录语义、`dimension` 或明确规则判断
4. 如果没有同名候选路径，写入未匹配报告
5. 如果多个候选路径仍无法唯一确定，写入歧义报告，不自动猜

建议异常输出：

```text
pipeline/reports/video_path_unresolved.csv
pipeline/reports/video_path_ambiguous.csv
```

## 3.4 q_category 统一规则

原始记录可能有：

- `task`
- `q_category`

最终统一成：

```text
q_category = task if task exists else q_category
```

处理后删除 `task` 字段。

如果两个字段同时存在且值不同：

- 优先使用 `task`
- 将原始 `q_category` 记录到 `source_q_category`
- 写入异常报告 `q_category_conflicts.csv`

如果没有冲突：

- 不生成 `source_q_category`

## 4. 问题字段整理规则

## 4.1 标准问题字段

最终每个问题只保留：

```text
question_key
question
options
answer
```

其中：

- `options` 只放干扰项
- `answer` 放正确答案文本
- `question_key` 必须存在且在同一条记录内唯一

## 4.2 结构 A 处理规则

原始结构：

```json
{
  "question_key": "Question-1",
  "question": "...",
  "options": ["A. 正确项", "B. 干扰项", "C. 干扰项", "D. 干扰项"],
  "answer": "正确项",
  "correct_option": "A"
}
```

处理规则：

1. 保留 `question_key`
2. 保留 `question`
3. 保留 `answer`
4. 根据 `correct_option` 或 `answer` 从 `options` 中删除正确选项
5. 删除 `correct_option`
6. 最终 `options` 只保留干扰项

正确选项删除策略：

1. 若有 `correct_option = "A"`，删除以 `A.` 或 `A. ` 开头的选项
2. 若选项没有标准字母前缀，则用 `answer` 文本匹配删除
3. 删除前统一清理选项前缀，用于比较：
   - `A. 文本`
   - `A 文本`
   - `A、文本`
4. 如果无法定位正确选项，写入异常报告，不强行删除

建议异常输出：

```text
pipeline/reports/correct_option_unresolved.csv
```

## 4.3 结构 B 处理规则

原始结构：

```json
{
  "question": "...",
  "ground_truth": "正确答案",
  "distractors": ["干扰项1", "干扰项2", "干扰项3"]
}
```

处理规则：

```text
answer = ground_truth
options = distractors
```

处理后删除：

```text
ground_truth
distractors
```

如果没有 `question_key`，按第 4.4 节构造。

## 4.4 question_key 构造规则

如果原始问题没有 `question_key`，需要根据 `q_category/task` 依次构造。

建议格式：

```text
{safe_q_category}-{index}
```

例如：

```text
Flow_and_Edge_Glide-1
Flow_and_Edge_Glide-2
Musical_Phrasing_Mapping-1
```

构造规则：

1. 取归一化后的 `q_category`
2. 将非字母数字字符统一替换成 `_`
3. 合并连续 `_`
4. 去掉首尾 `_`
5. 后缀使用同一条记录内的问题序号，从 1 开始

如果生成后与已有 `question_key` 冲突：

- 后缀追加 `_dup{n}`
- 写入异常报告 `question_key_conflicts.csv`

## 4.5 options 清洗规则

最终本地结构希望 `options` 是干扰项文本数组。

建议清洗：

1. 去掉首尾空白
2. 可选择去掉 `A. / B. / C. / D.` 前缀
3. 删除空字符串
4. 去重

是否去掉字母前缀需要统一。  
建议去掉，因为本地标注网站只审核答案文本，不依赖 ABCD 编号。

## 5. 与当前本地网站不兼容的接口字段记录

当前后端 `review_items` 数据表和 API 主要使用这些字段：

```text
video_id
video_uri
type
q_category
question
options
answer
status
is_delete
is_modified
_modifiedAt
_modified_by
```

整理后 QA 在异常情况下可能新增或保留的字段包括：

```text
video_path
dimension
source_video_path
source_q_category
```

其中 `source_video_path` 和 `source_q_category` 不是常规字段；没有冲突或异常时不生成。

这些字段当前不在本地接口模型中：

- `backend/app/main.py` 的 `ReviewItemResponse` 没有 `video_path`
- `review_items` 表没有 `video_path`
- `review_items` 表没有 `dimension`
- 前端 `ReviewItem` 类型没有 `video_path`
- 前端当前播放依赖 `video_uri`，不能直接使用磁盘 `video_path`

因此第一阶段只做 QA 归一化时，不应直接要求网站消费 `video_path`。  
网站导入阶段需要额外生成：

```text
video_uri
```

建议映射：

```text
video_path = /data4/.../IceArtBench/data/video/stsq/Eva1.webm
video_uri  = /media/video/stsq/Eva1.webm
```

## 6. 建议产物

第一阶段建议只生成文件，不改网站代码：

```text
pipeline/normalize_skating_qa.py
pipeline/reports/
data/Skating_and_Composition_QA_Normalized.jsonl
data/qa_normalization_summary.json
```

其中：

- `Skating_and_Composition_QA_Normalized.jsonl` 是整理后的主数据
- `qa_normalization_summary.json` 记录总数、成功数、异常数
- `pipeline/reports/*.csv` 记录所有无法自动处理的问题

## 7. 执行步骤

1. 读取 `path.md`，整理服务器视频索引规则
2. 读取原始 `data/Skating_and_Composition_QA_Final.jsonl`
3. 逐行解析 JSON
4. 统一顶层字段：
   - 补 `video_id`
   - 修正 `video_path`
   - `task` 合并到 `q_category`
5. 统一问题字段：
   - 结构 A 删除正确项和 `correct_option`
   - 结构 B 将 `ground_truth/distractors` 转成 `answer/options`
   - 缺失 `question_key` 时自动构造
6. 校验每条记录：
   - 是否有 `video_path`
   - 是否有 `video_id`
   - 是否有 `q_category`
   - 每个问题是否有 `question_key/question/options/answer`
   - `options` 是否只包含干扰项
7. 输出 normalized JSONL
8. 输出异常报告
9. 再进入本地网站导入脚本适配阶段

## 8. 验收标准

整理完成后应满足：

1. 每行 JSON 都能被解析
2. 每条记录都有 `video_path` 和 `video_id`
3. 每条记录只使用 `q_category`，不再使用 `task`
4. 每个问题都有 `question_key`
5. 每个问题只保留 `question_key/question/options/answer`
6. `ground_truth/distractors/correct_option` 不再出现在最终问题字段中
7. 结构 A 的正确选项已从 `options` 中删除
8. 无法匹配视频路径或正确选项的条目进入报告，不静默跳过

## 9. 后续接入标注前端前还需要解决的问题

QA 归一化后，还需要单独规划网站接入：

1. 当前网站需要 `video_uri`，而不是 `video_path`
2. 当前 DB schema 没有 `dimension/video_path`
3. 当前 `setup_local_db.py` 是基于旧 `question_pool.json` 和本地 `review_pools` 逻辑写的
4. 服务器数据量远大于本地样例，需要导入脚本支持 JSONL
5. `annotation_tool/` 和根目录 `data/` 的相对路径关系需要通过环境变量固定

建议顺序：

1. 先完成 QA 归一化
2. 再写视频路径与 `video_uri` 映射
3. 再改导入脚本
4. 最后改后端/前端字段
