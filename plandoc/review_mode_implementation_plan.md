# 花滑 QA 审核平台改造实现规划（从选择题模式切换到“正确选项审核”模式）

本文基于现有 [mvp_implementation_plan.md](file:///home/bella/Check/mvp_implementation_plan.md) 与 [implementation_steps.md](file:///home/bella/Check/implementation_steps.md) 的本地工程，重新规划平台目标、题池存储方式、共享保存逻辑与 UI 实现步骤。

新的平台目标不再是“让用户做选择题”，而是“让多个专家对照视频审核每个 QA 中正确选项的表述是否合理”，并对同一份动态问题池进行共享修改。

---

## 1. 新模式的业务目标

每个视频对应一条待审核 QA。专家打开视频后，需要判断该视频对应问题中的“正确选项”是否合理，并执行以下 3 种操作之一：

- `通过`：正确选项表述已经很好，无需修改
- `修改`：正确选项大体方向正确，但需要润色或修正
- `删除`：该问题或该正确选项质量太差，应标记删除

对应 UI 行为要求：

- 删除：点击删除按钮，立即标记为删除，导航题号变红
- 修改：只允许修改 `answer`，不允许编辑 3 个干扰项；双击正确选项进入编辑，点击确认后保存修改结果
- 通过：点击通过按钮，导航题号变绿

共享要求：

- 所有登录用户看到的是同一份动态问题池
- 任意用户执行删除 / 通过 / 修改确认后，其他用户刷新或重新拉取数据时都能看到最新结果
- 保存时记录修改用户和修改时间

---

## 2. 用户登录与注册逻辑

平台需要允许专家自行注册并登录，因此认证逻辑从“预置账号登录”调整为“姓名 + 密码注册登录”。

### 2.1 角色设计

- `user`
  - 默认角色
  - 所有注册用户都属于 `user`
  - 可查看共享动态问题池，并执行通过 / 修改 / 删除
- `admin`
  - 不开放前台注册
  - 由初始化脚本、数据库手工创建，或后续管理后台创建
  - 负责题池导入、用户管理、系统配置

### 2.2 用户表设计

建议数据库新增或完善 `users` 表：

- `id`
- `name`（专家姓名，登录账号）
- `password_hash`
- `role`（`user | admin`）
- `created_at`
- `updated_at`
- `last_login_at`（可选）
- `is_active`（默认 `true`）

约束建议：

- `name` 唯一
- `name` 去首尾空格后再保存
- 不允许空字符串
- 建议长度限制为 2-30 个字符

### 2.3 注册逻辑

注册方式：

- 用户输入：
  - 姓名
  - 密码

后端执行：

1. 校验姓名是否为空、是否重复
2. 校验密码长度（建议至少 6 位）
3. 使用安全哈希算法保存密码，不存明文
4. 自动写入：
   - `role = user`
   - `is_active = true`
5. 返回注册成功结果，可选地直接返回 token，或要求用户注册后再登录

推荐接口：

- `POST /api/v1/auth/register`

请求：

```json
{
  "name": "张三",
  "password": "abc12345"
}
```

返回：

```json
{
  "id": "uuid",
  "name": "张三",
  "role": "user"
}
```

推荐规则：

- 不开放用户自行选择角色
- 不开放匿名注册
- 同名不可重复注册

### 2.4 登录逻辑

登录方式：

- 使用 `name + password`

后端执行：

1. 用 `name` 查找用户
2. 校验密码哈希
3. 校验 `is_active`
4. 登录成功后签发 JWT
5. 更新 `last_login_at`

推荐接口：

- `POST /api/v1/auth/login`

请求：

```json
{
  "name": "张三",
  "password": "abc12345"
}
```

返回：

```json
{
  "access_token": "jwt",
  "token_type": "bearer",
  "role": "user",
  "name": "张三"
}
```

### 2.5 JWT 与会话建议

- 前端登录成功后把 `access_token` 存到 `localStorage`
- 后续请求通过 `Authorization: Bearer <token>` 访问接口
- 本地 MVP 可先只做 access token
- 后续部署到学校服务器时，可再补 refresh token 机制

token 内建议包含：

- `sub`：用户 id
- `name`
- `role`
- `iat`
- `exp`

### 2.6 前端页面流程

登录页改成两种模式切换：

- 登录
- 注册

注册区字段：

- 姓名
- 密码
- 确认密码

登录区字段：

- 姓名
- 密码

建议交互：

- 注册成功后自动切回登录模式，提示“注册成功，请登录”
- 也可以直接自动登录，但本地 MVP 为了简单，推荐先走“注册后登录”

### 2.7 审核行为与用户信息的关联

用户登录后，其身份需要参与所有审核写入：

- `questions[]._modified_by`
- `review_actions.operator_id`
- `review_actions.operator_name`

这样可以追踪：

- 谁通过了这道题
- 谁修改了正确选项
- 谁删除了这道题

---

## 3. 题池存储方式改造

新的存储方式需要明确区分两类数据：

- `origin`：原始问题池，只读，不再修改
- `dynamic`：动态共享问题池，专家操作都写到这里

### 2.1 origin 问题池格式

每个视频对应一个原始 JSON 文件，建议目录：

- `data/review_pools/origin/`

文件命名建议：

- `j1__Rhythm.json`
- `j2__Rhythm.json`
- `c45__Similarity.json`

格式：

```json
{
  "video_id": "j1",
  "type": "MS",
  "q_category": "Rhythm",
  "questions": [
    {
      "question_key": "Rhythm-1",
      "question": "问题题干",
      "options": [
        "干扰项1",
        "干扰项2",
        "干扰项3"
      ],
      "answer": "正确选项原始表述"
    }
  ]
}
```

说明：

- `video_id` 建议不带扩展名，实际视频文件仍可由 `video_id + .mp4` 映射
- 顶层是一个 `video_id + q_category` 对应的文件
- `questions[]` 下存该视频在该子问题下的所有问题
- 每个 question 都固定 3 个干扰项
- 每个 question 的 `answer` 单独存储，便于在 UI 中单独展示和编辑

### 2.2 dynamic 问题池格式

每个视频对应一个动态 JSON 文件，建议目录：

- `data/review_pools/dynamic/`

格式：

```json
{
  "video_id": "j1",
  "type": "MS",
  "q_category": "Rhythm",
  "questions": [
    {
      "question_key": "Rhythm-1",
      "question": "问题题干",
      "options": [
        "干扰项1",
        "干扰项2",
        "干扰项3"
      ],
      "answer": "当前生效的正确选项表述",
      "is_delete": false,
      "is_modified": false,
      "_modifiedAt": null,
      "_modified_by": null
    }
  ]
}
```

字段约束：

- `type`
  - 当前这批样例里：`j` 类视频可映射为 `MS`，`c` 类视频可映射为 `FS`
  - 后续不要把 `type` 和文件名前缀强绑定，应该由单独的映射配置或源数据字段提供
- `q_category`
  - 存子问题类别，如 `Energy`、`Rhythm`、`Similarity`、`Theme`、`Originality`
- `questions[].options`
  - 每个 question 只存 3 个干扰项，不可编辑
- `questions[].answer`
  - 当前审核后生效的正确选项，可编辑
- `questions[].is_delete`
  - 标记该题是否被删除
- `questions[].is_modified`
  - 标记该题的正确选项是否被修改过
- `questions[]._modifiedAt`
  - 最近一次删除或修改的时间
- `questions[]._modified_by`
  - 最近一次删除或修改的用户名或用户 id

---

## 4. 关于“通过”状态的补充建议

因此建议在 `dynamic` 数据中补充一个字段：

- `status`: `pending | passed | modified | deleted`

推荐最终格式：

```json
{
  "video_id": "j1",
  "type": "MS",
  "q_category": "Rhythm",
  "questions": [
    {
      "question_key": "Rhythm-1",
      "question": "问题题干",
      "options": ["干扰项1", "干扰项2", "干扰项3"],
      "answer": "当前生效的正确选项表述",
      "status": "passed",
      "is_delete": false,
      "is_modified": false,
      "_modifiedAt": "2026-05-15T10:20:00Z",
      "_modified_by": "expert_a"
    }
  ]
}
```

建议规则：

- 通过：`status = passed`
- 修改：`status = modified`
- 删除：`status = deleted`
- 初始：`status = pending`


## 5. 本地目录组织建议

建议把新模式的数据单独整理，避免和旧选择题模式混淆：

```text
data/
├── local_dev.db
├── review_pools/
│   ├── origin/
│   │   ├── j1__Rhythm.json
│   │   ├── j2__Rhythm.json
│   │   ├── j3__Rhythm.json
│   │   ├── c45__Similarity.json
│   │   ├── c46__Similarity.json
│   │   └── c47__Similarity.json
│   └── dynamic/
│       ├── j1__Rhythm.json
│       ├── j2__Rhythm.json
│       ├── j3__Rhythm.json
│       ├── c45__Similarity.json
│       ├── c46__Similarity.json
│       └── c47__Similarity.json
└── question_pool.json
```

说明：

- `origin/` 只在首次生成时写入
- `dynamic/` 用于导出当前共享状态
- 真正的共享读写建议走数据库，不建议多个用户并发直接改 JSON 文件

---

## 6. 实时共享的推荐实现方式

虽然需求表面上是“每个视频对应一个动态 JSON 问题池”，但为了满足多人实时共享，推荐采用：

- 文件负责“导入 / 导出 / 备份”
- 数据库负责“在线共享读写”

### 5.1 原因

如果直接把 `dynamic/*.json` 当成唯一数据源，会遇到：

- 多用户同时修改时容易互相覆盖
- 文件锁与并发写入处理复杂
- 无法方便记录历史、筛选和排序

因此推荐：

- `origin/*.json`：只读快照
- 数据库表 `review_items`：在线共享主数据源
- `dynamic/*.json`：从数据库导出得到的当前状态镜像

### 5.2 数据库作为动态池主存储

本地阶段仍可继续用 `SQLite`

后续上传服务器再迁移到 `PostgreSQL`

推荐新增表：

#### review_items

- `id`
- `file_key`（`video_id + "__" + q_category`，同文件下多个问题共用）
- `video_uri`
- `item_key`（`file_key + "__" + question_key`，单题唯一）
- `question_key`
- `type`
- `q_category`
- `question`
- `options_json`
- `answer`
- `status` (`pending | passed | modified | deleted`)
- `is_delete`
- `is_modified`
- `origin_snapshot_path`
- `dynamic_snapshot_path`
- `created_at`
- `updated_at`
- `_modifiedAt`
- `_modified_by`

#### review_actions

- `id`
- `review_item_id`
- `action_type` (`PASS | MODIFY | DELETE`)
- `before_answer`
- `after_answer`
- `operator_id`
- `operator_name`
- `created_at`

说明：

- `review_items` 存当前共享状态
- `review_actions` 存操作流水，便于回溯是谁在什么时候做了什么改动

---

## 7. 从旧问题池转换到新问题池的策略

你目前已有的 [question_pool.json](file:///home/bella/Check/data/question_pool.json) 结构是：

- 以 `pool_name` 分组
- 每题有多个 `options`
- 其中有一个 `correct_option_id`

现在要改为“每个视频一条 QA”，转换逻辑建议如下。

### 6.1 转换原则

以“每个 question 的正确选项视频 + 子问题”为文件键，生成一个 `review file`；文件中的每个问题再生成一条 `review item`

对于每道旧题：

1. 找到 `correct_option_id`
2. 拿到该正确选项对应的 `video_file`
3. 生成或追加到 `origin/{video_id}__{q_category}.json`
4. 该条数据的：
   - `question = stem`
   - `answer = 正确选项 text`
   - `options = 从同题其余选项中取 3 个干扰项 text`
   - 并写入该文件下的 `questions[]`

### 6.2 注意事项

如果不同子问题引用了同一个 `video_id`，会出现冲突：

- 一个视频可能对应多个 `q_category`
- 这说明“仅用 video_id 作为唯一键”可能不够

因此固定采用：

- `item_key = video_id + "_" + q_category`

或者文件命名改成：

- `j1_Rhythm.json`
- `j1_Energy.json`

如果你确认“一个视频只会对应一个子问题”，才可以只用 `video_id` 做唯一键。

---

## 8. 新的出题 / 拉题逻辑

新平台已经不是随机出题，而是“按筛选条件拉取待审核条目”。

### 7.1 不再需要的旧逻辑

以下逻辑可以废弃：

- 随机抽 3 个错误项 + 1 个正确项
- 生成 QuizInstance / QuizItems 固化 ABCD
- 用户选择 A/B/C/D 进行答题

### 7.2 新的拉取逻辑

前端进入审核页面后，应按筛选条件获取一组 `review items`

筛选条件建议支持：

- `type`
  - `MF`
  - `FF`
  - `MS`
  - `FS`
- `q_category`
  - `Energy`
  - `Rhythm`
  - `Similarity`
  - `Theme`
  - `Originality`

这就是你提到的：

- UI 选择上不止可以选择子问题


- 视频类型
- 子问题类别

### 7.3 返回给前端的数据结构

`GET /api/v1/review-items`

返回：

```json
{
  "items": [
    {
      "id": "uuid",
      "file_key": "c45__Similarity",
      "video_id": "j1",
      "video_uri": "/media/clips/j/j1.mp4",
      "question_key": "Rhythm-1",
      "type": "MS",
      "q_category": "Rhythm",
      "question": "问题题干",
      "options": [
        "干扰项1",
        "干扰项2",
        "干扰项3"
      ],
      "answer": "当前正确选项",
      "status": "pending",
      "is_delete": false,
      "is_modified": false,
      "_modifiedAt": null,
      "_modified_by": null
    }
  ]
}
```

---

## 9. UI 改造方案

### 8.1 左侧审核区的展示方式

左侧答题区不再是 4 个同权选项，而是拆成两块：

- `Question`
  - 展示题干
- `Correct Answer`
  - 单独展示正确选项
  - 双击进入编辑态
  - 编辑完成后点击“确认修改”保存
- `Distractor Options`
  - 展示 3 个干扰项，只读，不可编辑


### 8.2 操作按钮改造

原来的：

- 删除作答
- 确认提交

要改成：

- `删除`
- `通过`
- `确认修改`

行为定义：

- 删除
  - 立即写库
  - `status = deleted`
  - `is_delete = true`
  - `is_modified = true`
  - `_modifiedAt = now`（当前时间）
  - `_modified_by = 当前用户`
  - 导航按钮变红
- 通过
  - 立即写库
  - `status = passed`
  - 导航按钮变绿
- 确认修改
  - 只有正确选项进入编辑态且内容发生变化时可点
  - 写库：
    - `answer = 新文本`
    - `status = modified`
    - `is_modified = true`
    - `_modifiedAt = now`
    - `_modified_by = 当前用户`
  - 导航按钮建议变蓝或橙色，表示“已修改待复核”或“已修改”

### 8.3 导航颜色建议

- 当前选中题：蓝色外框
- 已通过：绿色
- 已删除：红色
- 已修改：橙色
- 未审核：灰色

如果只能保留红绿两类显著状态，也建议：

- 删除：红
- 通过：绿
- 修改：橙

---

## 10. 后端接口改造

### 9.1 初始化 / 拉取

- `POST /api/v1/review/init-from-origin`
  - 作用：从 `origin/*.json` 初始化数据库里的 `review_items`
  - 只在首次导入或重建环境时执行

- `POST /api/v1/auth/register`
  - 作用：姓名 + 密码注册
  - 默认创建 `user` 角色

- `POST /api/v1/auth/login`
  - 作用：姓名 + 密码登录并签发 JWT

- `GET /api/v1/auth/me`
  - 作用：获取当前登录用户信息

- `GET /api/v1/review-items`
  - 按 `type / q_category` 拉取当前共享条目

- `GET /api/v1/review-items/{item_id}`
  - 获取单条详情，用于刷新恢复

### 9.2 删除

- `POST /api/v1/review-items/{item_id}/delete`

后端执行：

- 更新 `review_items`
- 写 `review_actions`
- 导出或同步 `dynamic/{video_id}.json`

### 9.3 通过

- `POST /api/v1/review-items/{item_id}/pass`

后端执行：

- 更新 `status = passed`
- 写 `review_actions`
- 导出或同步 `dynamic/{video_id}.json`

### 9.4 修改

- `POST /api/v1/review-items/{item_id}/modify`

请求：

```json
{
  "answer": "修改后的正确选项文本"
}
```

后端执行：

- 更新 `answer`
- 更新 `status = modified`
- 更新 `is_modified / _modifiedAt / _modified_by`
- 写 `review_actions`
- 导出或同步 `dynamic/{video_id}.json`

---

## 11. 动态 JSON 的写回策略

推荐不要由前端直接改文件，而由后端统一写回。

### 10.1 每次操作后的写回顺序

1. 前端调用后端接口
2. 后端先更新数据库
3. 后端根据最新状态按 `file_key` 聚合覆盖写入 `dynamic/*.json`
4. 返回前端成功结果

这样可以保证：

- 数据库和动态 JSON 同步
- 所有用户共享的是同一份真实状态

### 10.2 动态文件命名建议

统一采用：

- `dynamic/j1__Rhythm.json`
- `dynamic/j1__Energy.json`

每个文件内部再用 `questions[]` 存放同文件下的所有问题。


---

## 12. 分阶段实现步骤

### 阶段 1：先把认证和数据结构改对

目标：先完成注册登录基础能力，以及 origin / dynamic 两套结构与数据库表

步骤：

1. 完善 `users` 表：
   - 支持 `name / password_hash / role / is_active`
2. 新增认证接口：
   - `POST /auth/register`
   - `POST /auth/login`
   - `GET /auth/me`
3. 新建目录：
   - `data/review_pools/origin/`
   - `data/review_pools/dynamic/`
4. 编写转换脚本：
   - 从旧 `data/question_pool.json` 生成每条视频的 `origin/*.json`
   - 在生成 `origin` 时就把 3 个干扰项固定下来，后续 `dynamic` 不再重算干扰项
5. 建数据库表：
   - `review_items`
   - `review_actions`
6. 编写初始化脚本：
   - 从 `origin/*.json` 初始化 `review_items`
   - 同时生成初始 `dynamic/*.json`

验收：

- 每条待审核 QA 都有一份只读 origin
- 数据库里有对应 review_items
- dynamic 文件已生成

### 阶段 2：后端接口替换旧“答题”接口

目标：从 quiz 模式切换到 review 模式

步骤：

1. 接入新的注册 / 登录接口
2. 新增：
   - `GET /api/v1/review-items`
   - `POST /api/v1/review-items/{id}/pass`
   - `POST /api/v1/review-items/{id}/delete`
   - `POST /api/v1/review-items/{id}/modify`
3. 弃用：
   - `quiz/start`
   - `log-interaction` 中与选择题强绑定的逻辑
4. 增加服务端写回 `dynamic/*.json` 的逻辑

验收：

- 调接口能查到共享题池
- 删除 / 通过 / 修改都能落库并写回 dynamic

### 阶段 3：前端 UI 改造成审核界面

目标：从“答题”变成“审核正确选项”

步骤：

1. 登录页改成：
   - 登录 / 注册双模式
   - 注册字段：姓名、密码、确认密码
2. 顶部筛选改成：
   - `type`
   - `q_category`
3. 左侧面板改成：
   - 题干
   - 3 个干扰项只读区
   - 正确选项编辑区
4. 按钮改成：
   - 删除
   - 通过
   - 确认修改
5. 双击正确选项进入编辑态
6. 操作成功后：
   - 当前条状态变化
   - 导航颜色更新
   - 自动切到下一条待审核项

验收：

- 用户不能编辑干扰项
- 用户可以双击编辑正确选项
- 删除 / 通过 / 修改后导航颜色立刻变化

### 阶段 4：多人共享与刷新恢复

目标：确保多用户协作可用

步骤：

1. 所有页面初始都从 `GET /review-items` 拉最新数据
2. 每次删除 / 通过 / 修改成功后，前端更新本地状态
3. 页面刷新后仍能显示最新共享状态
4. 后续可加轮询或 WebSocket，让不同用户几乎实时同步

MVP 建议：

- 先做“操作后重新拉取列表”
- 后续再加 WebSocket

验收：

- 用户 A 修改后，用户 B 刷新能看到新结果
- 用户 A 删除后，用户 B 刷新能看到红色删除状态

---

## 13. 需要同步修改的现有工程文件

建议后续实施时重点改这些位置：

- 后端
  - `backend/app/main.py`
  - 新增 `review` 路由和 sqlite 读写逻辑
  - 替换旧 quiz 相关查询
- 数据处理
  - `setup_local_db.py` 需要拆分为：
    - 旧题池转 origin
    - origin 初始化 dynamic / DB
- 前端
  - `frontend/src/App.tsx`
  - `frontend/src/App.css`
  - 把当前“选择题交互”替换成“审核交互”

---

## 14. 推荐的下一步执行顺序

按最稳的落地顺序，建议你下一步这样推进：

1. 先补齐 `users` 表和 `auth/register + auth/login`
2. 动态数据唯一键固定为 `video_id + q_category`
3. 写转换脚本，把旧 `question_pool.json` 变成 `origin/*.json`
4. 建 `review_items / review_actions` 两张表
5. 实现后端：
   - 列表拉取
   - 通过
   - 删除
   - 修改
6. 最后改前端 UI

如果只做本地 MVP，最先应该落地的是：

- `origin/dynamic` 目录结构
- 旧题池转新结构脚本
- `GET /review-items`
- `POST /pass` / `POST /delete` / `POST /modify`

这样能最快把平台从“选择题”切到“专家审核正确选项”模式。
