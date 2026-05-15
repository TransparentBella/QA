
# 交互式视频评测平台（花滑项目）系统设计与实施规划

我需要开发一个左侧答题、右侧视频的平台。核心需求是：用户的每一次交互动作（点击选项、点击红色删除、点击蓝色确认）都必须立即触发后端记录，而不仅仅是最后提交时保存。

Specific Requirements for "Click-to-Log":

审计粒度 (Logging Granularity):

选择动作： 记录用户点击了哪个选项（Option ID），即使他随后更改了选择。

删除动作： 记录点击“红色按钮”的时间及清空的操作。

确认动作： 记录点击“蓝色按钮”的时间及最终锁定的答案。

上下文记录： 每条记录必须自动附带：user_id（更改人）、question_id、video_timestamp（点击时视频播放到的位置）、action_type。

前端交互逻辑：

采用受控组件模式，用户点击选项后，立即发起异步 API 请求。

为了不影响用户操作的流畅度，需要实现乐观更新（Optimistic UI）或后端异步队列处理。

技术挑战点：

防抖与顺序： 如果用户快速连续点击不同选项，如何保证后端接收到的操作顺序与实际一致？

数据结构： 如何设计 ActivityLogs 表，以便后期可以完整回放（Replay）某个用户的答题路径？

Output Task:

数据模型设计： 提供 Questions、Choices 与 InteractionLogs 的数据库关系模型（SQL 或 NoSQL）。

API 接口设计： 重点设计一个 /log-interaction 接口，说明其接收的 Payload 结构。

前端实现思路： 给出 React/Vue 中处理“点击即记录”的高级函数封装示例（包含如何捕获当前视频播放时间点）。

性能优化方案： 针对高频点击入库，建议采用哪种策略（如：前端 Request Queue、后端 Redis 缓存等）来减轻主库压力。

## 1. 系统架构图 (System Architecture)

系统采用前后端分离架构，核心思想是“动静分离”与“异步高频写入”。

* **前端展示层 (React)：** 采用受控组件与乐观更新（Optimistic UI），拦截所有点击事件，将操作记录推入前端发送队列（Request Queue）。
* **后端逻辑层 (FastAPI)：** 处理 JWT 鉴权、题库生成与高频日志接收。采用全异步架构 (`async/await`) 避免 I/O 阻塞。
* **持久化层 (PostgreSQL)：** 作为唯一真实数据源。10人并发下，配合 SQLAlchemy `AsyncSession` 可实现无锁等待的高效写入。
* **媒体流服务 (Nginx)：** 专门负责右侧视频文件的点播与缓冲，释放后端 API 的网络带宽压力。

## 2. 数据库 Schema 设计 (PostgreSQL + SQLAlchemy)

核心原则是满足多租户（10 个账号左右）数据隔离，以及实现高粒度的操作审计（可回放）。

## 3. 前端交互与组件拆解 (React)

前端需要在保障极高流畅度的同时，完成高频暗埋点。

### 3.1 核心组件拆解

* **`QuizLayout`**: 页面级容器，管理全局状态（题号、用户 token、操作序列表）。
* **`QuestionArea` (左侧):**
* **`OptionList`**: 渲染 ABCD 选项。接收 `onOptionSelect` 回调。
* **`ActionButtons`**: 渲染“红底白字删除”与“蓝底白字确认”按钮。


* **`MediaPreview` (右侧):** 封装 `<video>` 标签。通过 `ref` 对外暴露 `getCurrentTime()` 方法。
* **`StatusBar` (底部):** 接收 `completed_questions` 数组，渲染圆形序号标识。已完成的加上 `bg-green-500` 类，未完成的为 `bg-gray-300` 类。

### 3.2 “点击即记录”前端实现思路 (防抖与请求队列)

采用**前端请求队列 (Request Queue)**，保障数据先后顺序：

```javascript
// React 伪代码：处理点击动作并保证顺序
let sequenceCounter = 0;
let requestQueue = Promise.resolve();

const handleAction = async (actionType, selectedLabel) => {
    // 1. 乐观更新：立刻让 UI 变色，不等待接口返回
    setLocalState(actionType, selectedLabel);
    
    // 2. 构造 Payload
    const payload = {
        question_id: currentQuestion.id,
        action_type: actionType, // 'SELECT' | 'CLEAR' | 'CONFIRM'
        selected_option: selectedLabel,
        video_time_offset: videoRef.current.currentTime,
        client_timestamp: Date.now(),
        sequence_number: ++sequenceCounter
    };

    // 3. 加入执行队列，确保上一个请求完成后再发下一个
    requestQueue = requestQueue.then(() => 
        api.post('/api/v1/log', payload)
    ).catch(err => {
        // 错误重试或提示逻辑
        console.error("日志上传失败", err);
    });
};

```

## 4. 后端 API 设计与目录结构 (FastAPI)

### 4.1 项目目录结构

```text
app/
├── main.py              # 入口文件
├── core/                # 配置与安全
│   ├── config.py
│   └── security.py      # JWT 与 OAuth2 逻辑
├── db/                  # 数据库连接
│   └── session.py       # AsyncSession 配置
├── models/              # SQLAlchemy 实体
│   └── models.py
├── schemas/             # Pydantic 校验模型
│   └── log_schemas.py   # Payload 结构定义
└── routers/             # 路由分发
    ├── auth.py          # 登录与 Token 发放
    ├── questions.py     # 题库生成与拉取
    └── logging.py       # 点击即记录 API

```

### 4.2 Endpoint 设计: `/api/v1/log`

这是承载核心并发请求的接口。

```python
# Pydantic Payload 验证模型
class LogInteractionRequest(BaseModel):
    question_id: UUID4
    action_type: str # 枚举: SELECT, CLEAR, CONFIRM
    selected_option: Optional[str]
    video_time_offset: float
    client_timestamp: int
    sequence_number: int

# 路由控制器
@router.post("/api/v1/log")
async def log_interaction(
    payload: LogInteractionRequest,
    current_user: User = Depends(get_current_active_user), # JWT 解析提取 User
    db: AsyncSession = Depends(get_db)
):
    # 构建数据库模型实体
    new_log = AuditLog(
        user_id=current_user.id, # 严格的租户隔离机制
        question_id=payload.question_id,
        action_type=payload.action_type,
        selected_option=payload.selected_option,
        video_time_offset=payload.video_time_offset,
        client_timestamp=payload.client_timestamp,
        sequence_number=payload.sequence_number
    )
    # 高频异步写入 PostgreSQL
    db.add(new_log)
    await db.commit() 
    return {"status": "success", "recorded_seq": payload.sequence_number}

```

## 5. 性能优化与并发处理

针对 10 人并发打分的场景，系统架构的应对策略如下：

1. **数据库直接承接（放弃 Redis 缓存）：** 对于 10 人级别、即便每秒点击 3 次，总并发也仅为 30 QPS。PostgreSQL 完全可以轻松应对每秒数千次的简单 `INSERT`。引入 Redis 会增加系统复杂度（双写一致性等问题），此处遵循“如无必要，勿增实体”原则，使用 SQLAlchemy的 `AsyncSession` 直接写库是最佳选择。
2. **前端请求顺序保证：** 通过上述的 `sequence_number` 机制，即便网络出现波动导致后发的请求先到，在进行后期数据回放与分析时，只需 `ORDER BY client_timestamp, sequence_number` 即可绝对还原用户的真实操作路径。

## 6. 部署规划与 MVP 路线图

### 6.1 服务器部署方案 (Uvicorn + Gunicorn + Docker)

采用 Gunicorn 作为进程管理器，管理多个 Uvicorn 的异步 worker，最大化利用 CPU 多核性能。

