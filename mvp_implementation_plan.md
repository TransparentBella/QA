# 交互式视频选择题平台（花滑项目）MVP 实现规划（本地 → 学校服务器）
  
目标：先做出「普通用户登录后可答题」的完整闭环页面（左侧答题、右侧视频），并满足“点击即记录（Click-to-Log）”审计要求；随后补齐管理员的题库/视频管理能力与部署。
  
本规划基于 [plan.md](file:///home/bella/Check/plan.md) 的交互审计约束与前后端分离思路（React + FastAPI + PostgreSQL）。
  
---
  
## 1. MVP 范围与验收标准
  
### 1.1 角色与页面
  
- 普通用户
  - 登录页：账号密码登录，获取 JWT
  - 答题页（核心）：左侧题干 + 4 个选项（ABCD）+ 红色删除 + 蓝色确认；右侧视频播放器；底部题号状态条
- 管理员（MVP 先做最小能力）
  - 登录（同一套鉴权）
  - 管理接口：导入题池 JSON（测试阶段可先用本地文件导入脚本代替管理 UI）
  
### 1.2 “点击即记录”验收（必须满足）
  
- 选择动作：每一次点击选项立刻产生一条日志（即使之后改选）
- 删除动作：点击红色删除立刻产生一条日志（并清空本题当前选择）
- 确认动作：点击蓝色确认立刻产生一条日志（并锁定本题答案）
- 每条日志自动附带：user_id、question_id、action_type
- 快速连点顺序可回放：服务端可按 `client_timestamp + sequence_number` 还原真实路径
  
---
  
## 2. 技术选型（推荐）
  
### 2.1 前端（React）
  
- React + Vite（开发快、部署简单）
- 状态管理：先用 React state + hooks；需要时再引入 Zustand/Redux
- HTTP：fetch/axios 均可，优先简单
- 关键点：受控组件 + 乐观更新 + 前端请求队列（保证日志顺序）
  
### 2.2 后端（FastAPI）
  
- FastAPI（async）
- JWT 鉴权（管理员与普通用户同一套 token，不同 role）
- SQLAlchemy Async + PostgreSQL
- 关键点：高频 `INSERT` 的日志表 + 可回放的业务表（QuizInstance / AnswerState）
  
### 2.3 媒体服务（本地 MVP）
  
- 本地先直接由后端或 Nginx 静态托管 mp4（即可）
- 学校服务器：建议 Nginx 托管视频，API 只提供鉴权与数据
  
---
  
## 3. 数据模型设计（兼顾“题库”与“回放日志”）
  
建议把“题库内容（Questions/Options）”和“用户行为日志（InteractionLogs）”分离：题库稳定、日志高频写入。
  
### 3.1 核心表（建议 PostgreSQL）
  
#### Users
  
- id (uuid)
- username (unique)
- password_hash
- role (`user` | `admin`)
- created_at
  
#### Pools（维度/题池）
  
- id (uuid)
- name（例如：能量的多样性与对比、节拍精准度与时机-子问题1）
- category_key（便于代码枚举：ENERGY_VARIETY、BEAT_ACCURACY_1 等）
- created_by（admin user_id）
- created_at
  
#### Videos（视频/片段资源）
  
- id (uuid)
- file_name（如 `wc2019_msfs_001.mp4`）
- uri（如 `/media/wc2019_msfs_001.mp4` 或对象存储地址）
- duration_sec（可选）
- meta_json（比赛信息、选手、节目类型等，可选）
  
#### Options（选项库：视频 + 专业评论）
  
- id (uuid)
- pool_id (fk Pools)
- video_id (fk Videos)
- label（展示用：通常用 file_name，也可用更友好名称）
- text（专业技术评论内容）
- created_at
  
#### Questions（题干 + 正确选项）
  
- id (uuid)
- pool_id (fk Pools)
- stem（题干）
- correct_option_id (fk Options)
- created_at
  
#### QuizInstances（一次“发卷/抽题”的实例：保证可复现）
  
- id (uuid)
- user_id (fk Users)
- pool_id (fk Pools) 或 session_id（如果一次混合多个维度，可建 Session + InstanceItems）
- seed（随机种子：用于复现选项排列/抽错项）
- created_at
- finished_at（可选）
  
#### QuizItems（实例中的每一道题：固化 A/B/C/D）
  
- id (uuid)
- quiz_instance_id (fk QuizInstances)
- question_id (fk Questions)
- option_ids（jsonb 存 `[A_id, B_id, C_id, D_id]`；或建 QuizItemOptions 表）
- correct_index（0-3，便于评分）
- order_index（第几题）
  
#### AnswerState（当前作答状态：用于渲染“本题选了啥/是否确认”）
  
- id (uuid)
- quiz_item_id (fk QuizItems)
- selected_index（0-3 或 null）
- confirmed (bool)
- confirmed_at
- updated_at
  
#### InteractionLogs（高频审计日志：Click-to-Log）
  
- id (uuid)
- user_id (fk Users)
- quiz_item_id (fk QuizItems) 或 question_id（建议实例级，避免题库重复引起歧义）
- action_type (`SELECT` | `CLEAR` | `CONFIRM`)
- selected_index（SELECT 时记录 0-3；CLEAR/CONFIRM 可为空）
- selected_option_id（SELECT/CONFIRM 时记录具体 option）
- client_timestamp_ms（bigint）
- sequence_number（int）
- client_event_id（uuid，幂等去重，建议前端生成）
- created_at（服务器落库时间）
  
索引建议：
  
- logs(user_id, created_at)
- logs(quiz_item_id, client_timestamp_ms, sequence_number)
- quiz_items(quiz_instance_id, order_index)
  
---
  
## 4. API 设计（MVP 必需接口）
  
建议以 `/api/v1` 为前缀，返回统一 JSON。
  
### 4.1 鉴权
  
- `POST /api/v1/auth/login`
  - req: `{ username, password }`
  - resp: `{ access_token, token_type, role }`
- `GET /api/v1/auth/me`
  - resp: `{ id, username, role }`
  
### 4.2 发卷/拉题
  
MVP 推荐把“抽题结果”固化为 QuizInstance + QuizItems，再返回给前端，保证：
  
- 每个用户的这一轮题目顺序固定
- 每道题 A/B/C/D 固定（复现、评分、回放一致）
  
接口：
  
- `POST /api/v1/quiz/start`
  - req: `{ pool_id, question_count }`
  - resp: `{ quiz_instance_id, items: [ { quiz_item_id, stem, options: [ { option_id, label, text, video_uri } ], order_index } ] }`
  
- `GET /api/v1/quiz/{quiz_instance_id}`
  - resp: 同上（用于刷新页面恢复）
  
### 4.3 点击即记录（核心）
  
- `POST /api/v1/log-interaction`
  - req:
  
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
  
处理规则（后端）：
  
- 先落 InteractionLogs（高频审计）
- 再更新 AnswerState（用于 UI 恢复）
  - SELECT：更新 selected_index（不改变 confirmed）
  - CLEAR：selected_index = null，confirmed = false
  - CONFIRM：confirmed = true，confirmed_at = now（可锁定不再允许 SELECT 或允许但记录异常）
  
返回：
  
```json
{ "status": "ok", "recorded_seq": 18 }
```
  
### 4.4 管理员导入题库（先做最小）
  
- `POST /api/v1/admin/import-pool`
  - 输入：题池 JSON（见第 6 节推荐结构）
  - 输出：pool_id + 导入统计
  
MVP 替代方案：写一个一次性导入脚本（读取 JSON，写入 DB），避免管理 UI 影响进度。
  
---
  
## 5. 前端页面与交互实现要点（普通用户答题界面）
  
### 5.1 组件拆分（与 plan.md 一致）
  
- QuizLayout：拉取 quiz_instance，维护当前题号、AnswerState、本地 sequence 计数器
- QuestionArea（左）
  - OptionList：渲染 A/B/C/D，受控选中态
  - ActionButtons：红色删除（CLEAR）、蓝色确认（CONFIRM）
- MediaPreview（右）
  - `<video>` + ref（仅用于播放与切题时定位）
- StatusBar（底）
  - 渲染题号圆点，已确认的高亮
  
### 5.2 前端“请求队列”保证顺序（核心）
  
目标：不阻塞 UI，仍能确保日志按用户点击顺序依次发送（或至少带序号可回放）。
  
实现原则：
  
- 每次点击立即更新 UI（乐观更新）
- 每次点击生成 `sequence_number += 1` 与 `client_event_id`
- 将请求串行化发送（Promise chain），避免并发乱序
- 若发送失败：本地暂存重试；但 UI 不回滚（日志缺失需在后台告警）
  
示例（伪代码）：
  
```ts
let sequenceCounter = 0;
let requestQueue: Promise<void> = Promise.resolve();
  
function enqueueLog(payload: any) {
  requestQueue = requestQueue
    .then(async () => {
      await api.post("/api/v1/log-interaction", payload);
    })
    .catch(async () => {
      await new Promise(r => setTimeout(r, 300));
      await api.post("/api/v1/log-interaction", payload);
    });
}
  
function buildBaseLog(quizItemId: string) {
  return {
    quiz_item_id: quizItemId,
    client_timestamp_ms: Date.now(),
    sequence_number: ++sequenceCounter,
    client_event_id: crypto.randomUUID()
  };
}
```
  
### 5.3 锁定与状态恢复
  
- 进入答题页：`GET /quiz/{id}` 拉回题目与 AnswerState
- 本题 CONFIRM 后：
  - UI 显示锁定（禁用选项/或允许但提示“已确认”并仍记录日志）
  - StatusBar 标记完成
  
---
  
## 6. 题库 JSON 结构（从你给的结构升级为“可维护”的结构）
  
你当前的结构把 `choice1/choice2/...` 直接塞在 question 里。对 50/57 个选项而言维护成本很高，且抽题时需要“全局选项库”。
  
推荐拆成两层：OptionBank（选项库）+ Questions（只存 correct_option_id）。
  
### 6.1 推荐导入结构（更适合 50/57 选项）
  
```json
{
  "pool_name": "能量的多样性与对比",
  "category_key": "ENERGY_VARIETY",
  "options": [
    {
      "option_id": "E01",
      "video_file": "wc2019_msfs_001.mp4",
      "text": "专业技术评论...",
      "meta": { "program": "MSFS", "season": 2019 }
    }
  ],
  "questions": [
    {
      "question_id": "Q01",
      "stem": "这段动作在能量强弱对比上最贴合哪条评论？",
      "correct_option_id": "E01"
    }
  ]
}
```
  
导入后落库映射：
  
- options[].option_id 只作为“导入用外部键”，落库后生成 uuid
- video_file → Videos
- options → Options（关联 pool_id 与 video_id）
- questions → Questions（correct_option_id 关联 Options）
  
### 6.2 兼容你现有结构（如果短期不想改）
  
你现有结构示例：
  
```json
[
  {
    "pool_name": "维度名称",
    "questions": [
      {
        "id": "唯一标识符",
        "stem": "问题题干",
        "choice1": { "label": "视频文件名.mp4", "text": "专业技术评论内容" },
        "choice2": { "label": "xx.mp4", "text": "..." }
      }
    ]
  }
]
```
  
如果继续用它，建议补充：
  
- question 内增加 `correct_choice_key`（例如 `"choice17"`），否则无法知道哪一个是“正确选项”
- 或者把 `choice` 改成数组并显式标注 `is_correct`
  
---
  
## 7. 抽题与出题逻辑设计（“1 正确 + 3 错误”）
  
你描述的方式：每道题从选项库随机抽取 3 个错误选项 + 正确选项，打乱组成 ABCD。
  
关键目标：
  
- 不重复：同一题的四个选项不能重复
- 可复现：同一次 QuizInstance 里每题的 ABCD 顺序固定
- 可回放：日志中的 selected_option_id 能对应到当时的 ABCD
  
### 7.1 推荐流程（服务端固化 QuizInstance）
  
输入：pool_id、question_count（比如 5）
  
1) 抽取 Questions：从该 pool 下随机取 N 道（或按固定顺序）
2) 为每道 question 生成 3 个 distractors：
   - 从 pool 的 Options 中随机抽 3 个，排除 correct_option
   - 若 pool 选项不足（< 4）：直接报错或降低题目选项数（不建议）
3) 组成 option_list = [correct + 3 wrong]，用种子打乱，得到 [A,B,C,D]
4) 写入 QuizInstance、QuizItems（固化 option_ids + correct_index）
5) 返回给前端
  
### 7.2 随机种子（保证复现）
  
- QuizInstance.seed = random_uuid 或 `hash(user_id + timestamp)`
- 每题洗牌 seed：`hash(quiz_seed + question_id)`
  
这样刷新页面/断网恢复仍能拿到同样的 ABCD（因为已固化在 QuizItems）。
  
### 7.3 避免“错误选项太像/太远”的策略（后续优化）
  
先做简单随机即可，后续可以加约束：
  
- distractors 限制来自同一子类（跳跃/旋转 vs chsq）
- 增加“相似度标签”（如 energy_level、beat_alignment），抽取相近但不相同的错误项，提高区分度
  
---
  
## 8. 如何准备测试数据（视频与问题）
  
你现在有两大来源：
  
- 跳跃/旋转：共享 50 个视频（WC 2019 男子 41 + 女子 6 + 其他 3）
- ChoreoSq/StepSeq：共享 57 个视频（hjx 48 + 自剪 9）
  
### 8.1 视频文件组织建议
  
建议把“完整节目”和“评分片段”分开，MVP 只用“评分片段”：
  
- `media/raw/`：原始整套节目（可选）
- `media/clips/`：用于答题的片段 mp4
  
命名规则建议包含关键信息（便于人工排查）：
  
- 跳跃/旋转：`wc2019_msfs_jr_001_jump1.mp4`
- chsq/step：`wc2019_wsfs_xx_012_chsq.mp4`
  
### 8.2 片段制作（ffmpeg）
  
如果你手头只有整套节目视频，建议用时间段裁切生成 clips：
  
```bash
ffmpeg -ss 00:02:13 -to 00:02:28 -i input.mp4 -c copy output_clip.mp4
```
  
原则：
  
- 片段 10–25 秒最适合做选择题（加载快、对比明显）
- 统一分辨率与码率可提升加载一致性（后续再做）
  
### 8.3 专业评论文本准备
  
每个 option 至少包含：
  
- `text`：一句可判别的评论（避免过长）
- `video_file`：对应片段文件名
  
你可以先用“占位评论”快速跑通流程，再逐步替换成真实评论。
  
---
  
## 9. 小型测试题池（建议你先做这个跑通网站）
  
目的：在不依赖全部 50/57 视频的情况下，先验证：
  
- 登录 → 发卷 → 播放视频 → 选项点击 → 删除 → 确认
- InteractionLogs 全量落库且可按 seq 回放
  
### 9.1 最小数据规模（推荐）
  
- 2 个 pool（各 2 道题）
  - pool A：能量的多样性与对比（options 8 个、questions 2 个）
  - pool B：节拍精准度与时机-子问题1（options 8 个、questions 2 个）
- 每题从各自 pool 抽 3 错 + 1 对即可
  
### 9.2 最小题池 JSON 示例（可直接导入的形状）
  
```json
{
  "pool_name": "能量的多样性与对比",
  "category_key": "ENERGY_VARIETY",
  "options": [
    { "option_id": "E01", "video_file": "energy_01.mp4", "text": "强弱对比明显，动作幅度随音乐起伏变化。" },
    { "option_id": "E02", "video_file": "energy_02.mp4", "text": "整体能量平稳，缺少显著的动态层次。" },
    { "option_id": "E03", "video_file": "energy_03.mp4", "text": "强拍处爆发力集中，但弱拍处理偏空。" },
    { "option_id": "E04", "video_file": "energy_04.mp4", "text": "音高变化对应上肢线条变化清晰。" },
    { "option_id": "E05", "video_file": "energy_05.mp4", "text": "节奏驱动强，但强弱对比不足。" },
    { "option_id": "E06", "video_file": "energy_06.mp4", "text": "动作与力度呈渐进式推进，高潮收束自然。" },
    { "option_id": "E07", "video_file": "energy_07.mp4", "text": "对比存在但过于依赖加速，缺少质感变化。" },
    { "option_id": "E08", "video_file": "energy_08.mp4", "text": "强拍爆发与弱拍呼吸形成清晰段落。" }
  ],
  "questions": [
    { "question_id": "Q_E_01", "stem": "这段动作在能量强弱对比上最贴合哪条评论？", "correct_option_id": "E04" },
    { "question_id": "Q_E_02", "stem": "以下哪条评论最准确描述该片段的能量层次？", "correct_option_id": "E08" }
  ]
}
```
  
把 `energy_01.mp4 ... energy_08.mp4` 放进 `media/clips/`，用任意短片段占位即可（哪怕重复也行，先跑通逻辑）。
  
---
  
## 10. 本地开发与部署路径（从 0 到可用）
  
### 10.1 本地开发（推荐顺序）
  
1) 启动 PostgreSQL（docker compose 最省事）
2) FastAPI：实现登录、发卷、log-interaction
3) React：实现登录页与答题页 UI
4) 导入最小题池（第 9 节）验证闭环
  
### 10.2 上传学校服务器（建议 Docker 化）
  
- Nginx：托管前端静态文件与视频文件
- FastAPI：docker 跑 uvicorn/gunicorn
- PostgreSQL：docker 或学校提供的数据库服务
  
生产建议：
  
- API 与 DB 分离网络
- 视频走 Nginx 静态目录（减少 API 带宽占用）
  
---
  
## 11. 你给出的 5 类问题如何落到 Pools（建议拆分方式）
  
你描述的维度包含子问题，建议每个“子问题”单独作为一个 pool（更清晰、抽题不串味）：
  
- 能量的多样性与对比（跳跃/旋转，50 options，5 questions）
- 节拍精准度与时机-子问题1（跳跃/旋转，50 options，5 questions）
- 节拍精准度与时机-子问题2（跳跃/旋转，50 options，5 questions）
- 动作与旋律的相似性（chsq/step，57 options，5 questions）
- 音乐主题的转化（chsq/step，57 options，5 questions）
- 原创性与创意表达（chsq/step，57 options，4 questions）
  
这样做的好处：
  
- 每个 pool 的 option_bank 固定（50 或 57），抽错项更一致
- 统计分析按维度/子问题分组更直接
  
---
  
## 12. 下一步（你可以按这个顺序推进）
  
- 先建一个最小题池（第 9 节）+ 8 个占位视频，跑通登录与答题页
- 后端先实现：login → start quiz → log-interaction
- 前端只做：登录页 + 答题页（选项/删除/确认 + 右侧视频）
- 满足 click-to-log 后，再导入完整 50/57 视频与 5 类问题池
