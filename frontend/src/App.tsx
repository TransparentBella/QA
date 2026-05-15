import './App.css'
import { useEffect, useMemo, useRef, useState } from 'react'

type Role = 'user' | 'admin'
type ActionType = 'SELECT' | 'CLEAR' | 'CONFIRM'

type LoginResponse = {
  access_token: string
  token_type: 'bearer'
  role: Role
}

type MeResponse = {
  id: string
  username: string
  role: Role
}

type QuizOption = {
  option_id: string
  label: string
  text: string
}

type QuizItem = {
  quiz_item_id: string
  stem: string
  video_uri: string
  options: QuizOption[]
  order_index: number
  selected_index: number | null
  confirmed: boolean
}

type QuizStartResponse = {
  quiz_instance_id: string
  items: QuizItem[]
}

async function apiJson<T>(
  path: string,
  init?: RequestInit & { token?: string; json?: unknown },
): Promise<T> {
  const headers = new Headers(init?.headers)
  headers.set('Accept', 'application/json')
  if (init?.json !== undefined) headers.set('Content-Type', 'application/json')
  if (init?.token) headers.set('Authorization', `Bearer ${init.token}`)

  const res = await fetch(path, {
    ...init,
    headers,
    body: init?.json !== undefined ? JSON.stringify(init.json) : init?.body,
  })

  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || `HTTP ${res.status}`)
  }

  return (await res.json()) as T
}

function randomUuid(): string {
  return crypto.randomUUID()
}

function App() {
  const [token, setToken] = useState<string>(() => localStorage.getItem('token') ?? '')
  const [me, setMe] = useState<MeResponse | null>(null)
  const [authError, setAuthError] = useState<string>('')

  const [username, setUsername] = useState('user')
  const [password, setPassword] = useState('user123')

  const [poolName, setPoolName] = useState<'Rhythm' | 'Similarity'>('Rhythm')
  const [questionCount, setQuestionCount] = useState<number>(2)
  const [quiz, setQuiz] = useState<QuizStartResponse | null>(null)
  const [currentIndex, setCurrentIndex] = useState<number>(0)

  const sequenceRef = useRef(0)
  const queueRef = useRef<Promise<void>>(Promise.resolve())

  const currentItem = useMemo(() => {
    if (!quiz) return null
    return quiz.items[currentIndex] ?? null
  }, [quiz, currentIndex])

  const confirmedCount = quiz ? quiz.items.filter((item) => item.confirmed).length : 0
  const selectedCount = quiz ? quiz.items.filter((item) => item.selected_index !== null).length : 0
  const progressPercent = quiz ? Math.round((confirmedCount / quiz.items.length) * 100) : 0

  useEffect(() => {
    if (!token) {
      setMe(null)
      return
    }
    apiJson<MeResponse>('/api/v1/auth/me', { token })
      .then(setMe)
      .catch(() => {
        setToken('')
        localStorage.removeItem('token')
        setMe(null)
      })
  }, [token])

  async function handleLogin() {
    setAuthError('')
    try {
      const res = await apiJson<LoginResponse>('/api/v1/auth/login', {
        method: 'POST',
        json: { username, password },
      })
      setToken(res.access_token)
      localStorage.setItem('token', res.access_token)
    } catch (e) {
      setAuthError(e instanceof Error ? e.message : '登录失败')
    }
  }

  function logout() {
    setToken('')
    localStorage.removeItem('token')
    setMe(null)
    setQuiz(null)
    setCurrentIndex(0)
  }

  async function startQuiz() {
    if (!token) return
    const res = await apiJson<QuizStartResponse>('/api/v1/quiz/start', {
      method: 'POST',
      token,
      json: { pool_name: poolName, question_count: questionCount },
    })
    setQuiz(res)
    setCurrentIndex(0)
    sequenceRef.current = 0
    queueRef.current = Promise.resolve()
  }

  function enqueueLog(payload: Record<string, unknown>) {
    if (!token) return
    queueRef.current = queueRef.current
      .then(async () => {
        await apiJson('/api/v1/log-interaction', { method: 'POST', token, json: payload })
      })
      .catch(async () => {
        await apiJson('/api/v1/log-interaction', { method: 'POST', token, json: payload })
      })
  }

  function baseLog(quizItemId: string, actionType: ActionType) {
    sequenceRef.current += 1
    return {
      quiz_item_id: quizItemId,
      action_type: actionType,
      client_timestamp_ms: Date.now(),
      sequence_number: sequenceRef.current,
      client_event_id: randomUuid(),
    }
  }

  function updateItem(partial: Partial<QuizItem>) {
    if (!quiz || !currentItem) return
    const nextItems = quiz.items.map((it) =>
      it.quiz_item_id === currentItem.quiz_item_id ? { ...it, ...partial } : it,
    )
    setQuiz({ ...quiz, items: nextItems })
  }

  function onSelect(index: number) {
    if (!currentItem || currentItem.confirmed) return
    const opt = currentItem.options[index]
    updateItem({ selected_index: index })
    enqueueLog({
      ...baseLog(currentItem.quiz_item_id, 'SELECT'),
      selected_index: index,
      selected_option_id: opt.option_id,
    })
  }

  function onClear() {
    if (!currentItem) return
    updateItem({ selected_index: null, confirmed: false })
    enqueueLog(baseLog(currentItem.quiz_item_id, 'CLEAR'))
  }

  function onConfirm() {
    if (!currentItem || currentItem.confirmed) return
    if (currentItem.selected_index === null) return
    const idx = currentItem.selected_index
    const opt = currentItem.options[idx]
    setQuiz((prev) => {
      if (!prev) return prev
      const nextItems = prev.items.map((it) =>
        it.quiz_item_id === currentItem.quiz_item_id ? { ...it, confirmed: true } : it,
      )
      return { ...prev, items: nextItems }
    })
    enqueueLog({
      ...baseLog(currentItem.quiz_item_id, 'CONFIRM'),
      selected_index: idx,
      selected_option_id: opt.option_id,
    })
    if (quiz && currentIndex < quiz.items.length - 1) {
      const nextIndex = currentIndex + 1
      window.requestAnimationFrame(() => {
        setCurrentIndex(nextIndex)
      })
    }
  }

  const currentOptionLabel =
    currentItem && currentItem.selected_index !== null
      ? String.fromCharCode(65 + currentItem.selected_index)
      : '--'

  return (
    <div className="app">
      {!token ? (
        <div className="auth-shell">
          <div className="auth-card">
            <div className="auth-brand">
              <div className="brand-kicker">Interactive Review Platform</div>
              <h1>花滑视频选择题评测系统</h1>
              <p>
                面向本地 MVP 的专业评测界面。左侧完成题目判断，右侧同步查看片段，所有交互即时记录。
              </p>
            </div>

            <div className="auth-form">
              <div className="panel-title">
                <span className="panel-title-mark" />
                用户登录
              </div>
              <div className="form-row">
                <label>用户名</label>
                <input value={username} onChange={(e) => setUsername(e.target.value)} />
              </div>
              <div className="form-row">
                <label>密码</label>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </div>
              {authError ? <div className="error">{authError}</div> : null}
              <button className="btn primary wide" onClick={handleLogin}>
                进入评测
              </button>
              <div className="auth-tips">
                <span>普通用户：user / user123</span>
                <span>管理员：admin / admin123</span>
              </div>
            </div>
          </div>
        </div>
      ) : (
        <div className="workspace">
          <header className="workspace-topbar">
            <div className="workspace-tabs">
              <button className="top-tab">C52</button>
              <button className="top-tab">WL</button>
              <button className="top-tab">QW2</button>
              <button className="top-tab">W1</button>
              <button className="top-tab active">Normal</button>
              <button className="top-tab">Correction</button>
            </div>

            <div className="workspace-brand">
              <div className="brand-badge">FS</div>
              <div>
                <div className="workspace-title">花滑交互式评测平台</div>
                <div className="workspace-subtitle">Question Review Workspace</div>
              </div>
            </div>

            <div className="workspace-center">
              <div className="control-group">
                <span className="control-label">题池</span>
                <select value={poolName} onChange={(e) => setPoolName(e.target.value as 'Rhythm' | 'Similarity')}>
                  <option value="Rhythm">Rhythm</option>
                  <option value="Similarity">Similarity</option>
                </select>
              </div>
              <div className="control-group compact">
                <span className="control-label">题数</span>
                <input
                  type="number"
                  min={1}
                  max={50}
                  value={questionCount}
                  onChange={(e) => setQuestionCount(Number(e.target.value))}
                />
              </div>
              <button className="btn primary" onClick={startQuiz}>
                生成试题
              </button>
            </div>

            <div className="workspace-right">
              <div className="user-chip">
                <span className="status-dot" />
                {me?.username} / {me?.role}
              </div>
              <button className="btn" onClick={logout}>
                退出
              </button>
            </div>
          </header>

          <div className="workspace-stats">
            <div className="stat-card">
              <div className="stat-label">当前题池</div>
              <div className="stat-value">{poolName}</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">已选题目</div>
              <div className="stat-value">{selectedCount}</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">已确认</div>
              <div className="stat-value">{confirmedCount}</div>
            </div>
            <div className="stat-card wide">
              <div className="stat-label">整体进度</div>
              <div className="progress-inline">
                <div className="progress-track">
                  <div className="progress-bar" style={{ width: `${progressPercent}%` }} />
                </div>
                <span>{progressPercent}%</span>
              </div>
            </div>
          </div>

          {quiz && currentItem ? (
            <>
              <div className="review-layout">
                <aside className="review-panel">
                  <div className="review-panel-scroll">
                    <div className="left-mini-toolbar">
                      <span className="mini-chip active">Question</span>
                      <span className="mini-chip">Choices</span>
                      <span className="mini-chip">Review</span>
                      <span className="mini-chip">Notes</span>
                    </div>

                    <div className="panel-header">
                      <div className="panel-title">
                        <span className="panel-title-mark" />
                        题目评测区
                      </div>
                      <div className="panel-meta">
                        <span className="meta-pill active">Q {currentItem.order_index + 1}</span>
                        <span className={`meta-pill ${currentItem.confirmed ? 'done' : ''}`}>
                          {currentItem.confirmed ? 'Confirmed' : 'Pending'}
                        </span>
                      </div>
                    </div>

                    <div className="question-card">
                      <div className="question-path">
                        <span>{poolName}</span>
                        <span>/</span>
                        <span>Question {currentItem.order_index + 1}</span>
                      </div>
                      <div className="question-title">{currentItem.stem}</div>
                    </div>

                    <div className="selection-summary">
                      <div className="summary-item">
                        <span className="summary-label">当前选择</span>
                        <span className="summary-value">{currentOptionLabel}</span>
                      </div>
                      <div className="summary-item">
                        <span className="summary-label">日志序号</span>
                        <span className="summary-value">{sequenceRef.current}</span>
                      </div>
                    </div>

                    <div className="options-list">
                      {currentItem.options.map((opt, idx) => {
                        const selected = currentItem.selected_index === idx
                        const disabled = currentItem.confirmed
                        return (
                          <button
                            key={opt.option_id}
                            className={`option-card ${selected ? 'selected' : ''}`}
                            disabled={disabled}
                            onClick={() => onSelect(idx)}
                          >
                            <div className="option-head">
                              <div className="option-badge">{String.fromCharCode(65 + idx)}</div>
                            </div>
                            <div className="option-body">{opt.text}</div>
                          </button>
                        )
                      })}
                    </div>

                    <div className="left-note-card">
                      <div className="left-note-title">评测说明</div>
                      <div className="left-note-text">
                        请选择最符合当前视频片段的专业评论。点击选项即时记录，确认后锁定本题答案。
                      </div>
                    </div>

                    <div className="panel-actions">
                      <button className="btn danger slim" onClick={onClear}>
                        删除选择
                      </button>
                      <button
                        className="btn primary slim"
                        onClick={onConfirm}
                        disabled={currentItem.confirmed || currentItem.selected_index === null}
                      >
                        确认提交
                      </button>
                    </div>
                  </div>
                </aside>

                <main className="video-stage">
                  <div className="stage-header">
                    <div>
                      <div className="panel-title">
                        <span className="panel-title-mark" />
                        视频观察区
                      </div>
                      <div className="stage-subtitle">播放当前题目对应片段，完成左侧判断</div>
                    </div>
                    <div className="stage-tags">
                      <span className="meta-pill">Clip</span>
                      <span className="meta-pill">{poolName}</span>
                    </div>
                  </div>

                  <div className="video-shell">
                    <div className="video-overlay-bar">
                      <div className="overlay-group">
                        <span className="overlay-badge warning">Q{currentItem.order_index + 1}</span>
                        <span className="overlay-badge">{poolName}</span>
                      </div>
                      <div className="overlay-group">
                        <span className="overlay-badge success">{confirmedCount}/{quiz.items.length}</span>
                      </div>
                    </div>
                    <video className="video-player" controls src={currentItem.video_uri} />
                  </div>

                  <div className="stage-footer">
                    <div className="stage-info">
                      <span className="stage-info-label">当前片段</span>
                      <span className="stage-info-value">{currentItem.video_uri.split('/').pop()}</span>
                    </div>
                  </div>
                </main>
              </div>

              <div className="navigator-card">
                <div className="navigator-header">
                  <div className="panel-title">
                    <span className="panel-title-mark" />
                    题目导航
                  </div>
                  <div className="navigator-text">点击编号快速切换题目</div>
                </div>

                <div className="navigator-grid">
                  {quiz.items.map((it, idx) => (
                    <button
                      key={it.quiz_item_id}
                      className={`nav-tile ${idx === currentIndex ? 'active' : ''} ${
                        it.confirmed ? 'done' : ''
                      }`}
                      onClick={() => setCurrentIndex(idx)}
                      title={`第 ${idx + 1} 题`}
                    >
                      <span className="nav-index">{idx + 1}</span>
                    </button>
                  ))}
                </div>
              </div>
            </>
          ) : (
            <div className="empty-state">
              <div className="empty-title">准备开始一轮评测</div>
              <div className="empty-text">选择题池与题数后，点击“生成试题”进入左题右视频的评测界面。</div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default App
