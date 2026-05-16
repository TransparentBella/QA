import './App.css'
import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'

type Role = 'user' | 'admin'
type ReviewStatus = 'pending' | 'passed' | 'modified' | 'deleted'

type LoginResponse = {
  access_token: string
  token_type: 'bearer'
  role: Role
  name: string
}

type RegisterResponse = {
  id: string
  name: string
  role: Role
}

type MeResponse = {
  id: string
  name: string
  role: Role
}

type ReviewItem = {
  id: string
  item_key: string
  video_id: string
  video_uri: string
  type: string
  q_category: string
  question: string
  options: string[]
  answer: string
  status: ReviewStatus
  is_delete: boolean
  is_modified: boolean
  _modifiedAt: string | null
  _modified_by: string | null
}

type ReviewItemsResponse = {
  items: ReviewItem[]
}

const STATUS_TEXT: Record<ReviewStatus, string> = {
  pending: '待审核',
  passed: '已通过',
  modified: '已修改',
  deleted: '已删除',
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
    try {
      const parsed = JSON.parse(text) as { detail?: string }
      throw new Error(parsed.detail || text || `HTTP ${res.status}`)
    } catch {
      throw new Error(text || `HTTP ${res.status}`)
    }
  }

  return (await res.json()) as T
}

function App() {
  const [token, setToken] = useState<string>(() => localStorage.getItem('token') ?? '')
  const [me, setMe] = useState<MeResponse | null>(null)
  const [authError, setAuthError] = useState('')
  const [authSuccess, setAuthSuccess] = useState('')
  const [pageError, setPageError] = useState('')
  const [authLoading, setAuthLoading] = useState(false)

  const [authMode, setAuthMode] = useState<'login' | 'register'>('login')
  const [name, setName] = useState('')
  const [password, setPassword] = useState('')
  const [registerName, setRegisterName] = useState('')
  const [registerPassword, setRegisterPassword] = useState('')
  const [registerConfirmPassword, setRegisterConfirmPassword] = useState('')

  const [items, setItems] = useState<ReviewItem[]>([])
  const [selectedType, setSelectedType] = useState('ALL')
  const [selectedCategory, setSelectedCategory] = useState('ALL')
  const [currentIndex, setCurrentIndex] = useState(0)
  const [isEditingAnswer, setIsEditingAnswer] = useState(false)
  const [draftAnswer, setDraftAnswer] = useState('')
  const [busyAction, setBusyAction] = useState<'pass' | 'delete' | 'modify' | ''>('')

  const panelScrollRef = useRef<HTMLDivElement | null>(null)

  const typeOptions = useMemo(() => ['ALL', ...Array.from(new Set(items.map((item) => item.type)))], [items])
  const categoryOptions = useMemo(
    () => ['ALL', ...Array.from(new Set(items.map((item) => item.q_category)))],
    [items],
  )

  const filteredItems = useMemo(
    () =>
      items.filter((item) => {
        const matchType = selectedType === 'ALL' || item.type === selectedType
        const matchCategory = selectedCategory === 'ALL' || item.q_category === selectedCategory
        return matchType && matchCategory
      }),
    [items, selectedType, selectedCategory],
  )

  const currentItem = filteredItems[currentIndex] ?? null
  const passedCount = items.filter((item) => item.status === 'passed').length
  const deletedCount = items.filter((item) => item.status === 'deleted').length
  const modifiedCount = items.filter((item) => item.status === 'modified').length

  useEffect(() => {
    if (!token) {
      setMe(null)
      setItems([])
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

  useEffect(() => {
    if (!token) return
    loadReviewItems().catch((error) => {
      setPageError(error instanceof Error ? error.message : '加载审核数据失败')
    })
  }, [token])

  useEffect(() => {
    setCurrentIndex(0)
  }, [selectedType, selectedCategory])

  useEffect(() => {
    if (currentIndex >= filteredItems.length) {
      setCurrentIndex(filteredItems.length > 0 ? filteredItems.length - 1 : 0)
    }
  }, [filteredItems.length, currentIndex])

  useEffect(() => {
    panelScrollRef.current?.scrollTo({ top: 0, behavior: 'smooth' })
    setIsEditingAnswer(false)
    setDraftAnswer(currentItem?.answer ?? '')
  }, [currentIndex, currentItem?.id])

  async function loadReviewItems() {
    if (!token) return
    setPageError('')
    const response = await apiJson<ReviewItemsResponse>('/api/v1/review-items', { token })
    setItems(response.items)
  }

  async function handleLogin() {
    setAuthError('')
    setAuthSuccess('')
    setAuthLoading(true)
    try {
      const res = await apiJson<LoginResponse>('/api/v1/auth/login', {
        method: 'POST',
        json: { name, password },
      })
      setToken(res.access_token)
      localStorage.setItem('token', res.access_token)
    } catch (e) {
      setAuthError(e instanceof Error ? e.message : '登录失败')
    } finally {
      setAuthLoading(false)
    }
  }

  async function handleRegister() {
    setAuthError('')
    setAuthSuccess('')
    setAuthLoading(true)
    if (registerPassword !== registerConfirmPassword) {
      setAuthError('两次输入的密码不一致')
      setAuthLoading(false)
      return
    }
    try {
      await apiJson<RegisterResponse>('/api/v1/auth/register', {
        method: 'POST',
        json: { name: registerName, password: registerPassword },
      })
      setAuthMode('login')
      setName(registerName.trim())
      setPassword('')
      setRegisterName('')
      setRegisterPassword('')
      setRegisterConfirmPassword('')
      setAuthSuccess('注册成功，请登录')
    } catch (e) {
      setAuthError(e instanceof Error ? e.message : '注册失败')
    } finally {
      setAuthLoading(false)
    }
  }

  async function handleAuthSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (authLoading) return
    if (authMode === 'login') {
      await handleLogin()
    } else {
      await handleRegister()
    }
  }

  function logout() {
    setToken('')
    localStorage.removeItem('token')
    setMe(null)
    setItems([])
    setSelectedType('ALL')
    setSelectedCategory('ALL')
    setCurrentIndex(0)
    setIsEditingAnswer(false)
    setDraftAnswer('')
  }

  function replaceItem(updatedItem: ReviewItem) {
    setItems((prev) => prev.map((item) => (item.id === updatedItem.id ? updatedItem : item)))
  }

  function advanceToNextItem() {
    setCurrentIndex((prev) => (filteredItems.length > 0 ? Math.min(prev + 1, filteredItems.length - 1) : 0))
  }

  async function handlePass() {
    if (!token || !currentItem) return
    setBusyAction('pass')
    setPageError('')
    try {
      const updated = await apiJson<ReviewItem>(`/api/v1/review-items/${currentItem.id}/pass`, {
        method: 'POST',
        token,
      })
      replaceItem(updated)
      advanceToNextItem()
    } catch (error) {
      setPageError(error instanceof Error ? error.message : '通过操作失败')
    } finally {
      setBusyAction('')
    }
  }

  async function handleDelete() {
    if (!token || !currentItem) return
    setBusyAction('delete')
    setPageError('')
    try {
      const updated = await apiJson<ReviewItem>(`/api/v1/review-items/${currentItem.id}/delete`, {
        method: 'POST',
        token,
      })
      replaceItem(updated)
      advanceToNextItem()
    } catch (error) {
      setPageError(error instanceof Error ? error.message : '删除操作失败')
    } finally {
      setBusyAction('')
    }
  }

  async function handleModify() {
    if (!token || !currentItem) return
    const trimmed = draftAnswer.trim()
    if (!trimmed || trimmed === currentItem.answer.trim()) return

    setBusyAction('modify')
    setPageError('')
    try {
      const updated = await apiJson<ReviewItem>(`/api/v1/review-items/${currentItem.id}/modify`, {
        method: 'POST',
        token,
        json: { answer: trimmed },
      })
      replaceItem(updated)
      setIsEditingAnswer(false)
      setDraftAnswer(updated.answer)
      advanceToNextItem()
    } catch (error) {
      setPageError(error instanceof Error ? error.message : '修改保存失败')
    } finally {
      setBusyAction('')
    }
  }

  const canModify = !!currentItem && draftAnswer.trim().length > 0 && draftAnswer.trim() !== currentItem.answer.trim()

  return (
    <div className="app">
      {!token ? (
        <div className="auth-shell">
          <div className="auth-card">
            <div className="auth-brand">
              <div className="brand-kicker">Interactive Review Platform</div>
              <h1>花滑 QA 正确选项审核平台</h1>
              <p>多个专家围绕同一份动态问题池，对正确选项表述进行通过、修改或删除审核。</p>
            </div>

            <form className="auth-form" onSubmit={(e) => void handleAuthSubmit(e)}>
              <div className="auth-mode-switch">
                <button
                  type="button"
                  className={`auth-mode-btn ${authMode === 'login' ? 'active' : ''}`}
                  onClick={() => {
                    setAuthMode('login')
                    setAuthError('')
                    setAuthSuccess('')
                  }}
                >
                  登录
                </button>
                <button
                  type="button"
                  className={`auth-mode-btn ${authMode === 'register' ? 'active' : ''}`}
                  onClick={() => {
                    setAuthMode('register')
                    setAuthError('')
                    setAuthSuccess('')
                  }}
                >
                  注册
                </button>
              </div>
              <div className="panel-title">
                <span className="panel-title-mark" />
                {authMode === 'login' ? '专家登录' : '专家注册'}
              </div>
              {authMode === 'login' ? (
                <>
                  <div className="form-row">
                    <label>姓名</label>
                    <input value={name} onChange={(e) => setName(e.target.value)} placeholder="请输入姓名" />
                  </div>
                  <div className="form-row">
                    <label>密码</label>
                    <input
                      type="password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      placeholder="请输入密码"
                    />
                  </div>
                </>
              ) : (
                <>
                  <div className="form-row">
                    <label>姓名</label>
                    <input
                      value={registerName}
                      onChange={(e) => setRegisterName(e.target.value)}
                      placeholder="请输入真实姓名"
                    />
                  </div>
                  <div className="form-row">
                    <label>密码</label>
                    <input
                      type="password"
                      value={registerPassword}
                      onChange={(e) => setRegisterPassword(e.target.value)}
                      placeholder="至少 6 位"
                    />
                  </div>
                  <div className="form-row">
                    <label>确认密码</label>
                    <input
                      type="password"
                      value={registerConfirmPassword}
                      onChange={(e) => setRegisterConfirmPassword(e.target.value)}
                      placeholder="再次输入密码"
                    />
                  </div>
                </>
              )}
              {authError ? <div className="error">{authError}</div> : null}
              {authSuccess ? <div className="success">{authSuccess}</div> : null}
              <button
                type="submit"
                className="btn primary wide"
                disabled={authLoading}
              >
                {authLoading ? '处理中...' : authMode === 'login' ? '进入审核' : '完成注册'}
              </button>
              <div className="auth-tips">
                <span>专家用户可直接使用姓名 + 密码注册</span>
                <span>管理员保留账号：admin / admin123</span>
              </div>
            </form>
          </div>
        </div>
      ) : (
        <div className="workspace">
          <header className="workspace-topbar">
            <div className="workspace-brand">
              <div className="brand-badge">QA</div>
              <div>
                <div className="workspace-title">花滑 QA 审核工作台</div>
                <div className="workspace-subtitle">Correct Answer Review Workspace</div>
              </div>
            </div>

            <div className="workspace-center">
              <div className="control-group">
                <span className="control-label">比赛类型</span>
                <select value={selectedType} onChange={(e) => setSelectedType(e.target.value)}>
                  {typeOptions.map((option) => (
                    <option key={option} value={option}>
                      {option === 'ALL' ? '全部' : option}
                    </option>
                  ))}
                </select>
              </div>
              <div className="control-group">
                <span className="control-label">任务</span>
                <select value={selectedCategory} onChange={(e) => setSelectedCategory(e.target.value)}>
                  {categoryOptions.map((option) => (
                    <option key={option} value={option}>
                      {option === 'ALL' ? '全部' : option}
                    </option>
                  ))}
                </select>
              </div>
              <button className="btn primary" onClick={() => void loadReviewItems()}>
                同步最新数据
              </button>
            </div>

            <div className="workspace-right">
              <div className="user-chip">
                <span className="status-dot" />
                {me?.name} / {me?.role}
              </div>
              <button className="btn" onClick={logout}>
                退出
              </button>
            </div>
          </header>

          <div className="workspace-stats review-stats">
            <div className="stat-card">
              <div className="stat-label">当前类型</div>
              <div className="stat-value">{selectedType === 'ALL' ? '全部' : selectedType}</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">当前任务</div>
              <div className="stat-value">{selectedCategory === 'ALL' ? '全部' : selectedCategory}</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">已通过</div>
              <div className="stat-value">{passedCount}</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">已修改 / 已删除</div>
              <div className="stat-value">
                {modifiedCount} / {deletedCount}
              </div>
            </div>
          </div>

          {pageError ? <div className="page-error">{pageError}</div> : null}

          {currentItem ? (
            <>
              <div className="review-layout">
                <aside className="review-panel">
                  <div className="review-panel-scroll" ref={panelScrollRef}>
                    <div className="panel-header">
                      <div className="panel-title">
                        <span className="panel-title-mark" />
                        审核区
                      </div>
                      <div className="panel-meta">
                        <span className="meta-pill active">Q {currentIndex + 1}</span>
                        <span className={`meta-pill status-${currentItem.status}`}>{STATUS_TEXT[currentItem.status]}</span>
                      </div>
                    </div>

                    <div className="question-card">
                      <div className="question-path">
                        <span>{currentItem.type}</span>
                        <span>/</span>
                        <span>{currentItem.q_category}</span>
                        <span>/</span>
                        <span>{currentItem.video_id}</span>
                      </div>
                      <div className="question-title">{currentItem.question}</div>
                    </div>

                    <div className="selection-summary review-summary">
                      <div className="summary-item">
                        <span className="summary-label">当前状态</span>
                        <span className="summary-value">{STATUS_TEXT[currentItem.status]}</span>
                      </div>
                      <div className="summary-item">
                        <span className="summary-label">最近修改</span>
                        <span className="summary-value">{currentItem._modified_by ?? '--'}</span>
                      </div>
                    </div>

                    <section className="answer-section">
                      <div className="section-label">正确选项</div>
                      <div
                        className={`answer-card ${isEditingAnswer ? 'editing' : ''}`}
                        onDoubleClick={() => {
                          setIsEditingAnswer(true)
                          setDraftAnswer(currentItem.answer)
                        }}
                      >
                        {isEditingAnswer ? (
                          <textarea
                            className="answer-editor"
                            value={draftAnswer}
                            onChange={(e) => setDraftAnswer(e.target.value)}
                          />
                        ) : (
                          <div className="answer-text">{currentItem.answer}</div>
                        )}
                      </div>
                      <div className="section-hint">双击正确选项进入编辑，仅该区域可修改。</div>
                    </section>

                    <section className="distractor-section">
                      <div className="section-label">干扰选项</div>
                      <div className="options-list readonly-options">
                        {currentItem.options.map((optionText, idx) => (
                          <div key={`${currentItem.item_key}-${idx}`} className="option-card readonly">
                            <div className="option-body">{optionText}</div>
                          </div>
                        ))}
                      </div>
                    </section>
                  </div>
                </aside>

                <main className="video-stage">
                  <div className="stage-header">
                    <div>
                      <div className="panel-title">
                        <span className="panel-title-mark" />
                        视频观察区
                      </div>
                      <div className="stage-subtitle">结合右侧视频判断正确选项表述是否合理。</div>
                    </div>
                    <div className="stage-tags">
                      <span className="meta-pill">{currentItem.type}</span>
                      <span className="meta-pill">{currentItem.q_category}</span>
                    </div>
                  </div>

                  <div className="video-shell">
                    <div className="video-overlay-bar">
                      <div className="overlay-group">
                        <span className="overlay-badge warning">{currentItem.video_id}</span>
                        <span className="overlay-badge">{currentItem.q_category}</span>
                      </div>
                      <div className="overlay-group">
                        <span className={`overlay-badge overlay-${currentItem.status}`}>{STATUS_TEXT[currentItem.status]}</span>
                      </div>
                    </div>
                    <video key={currentItem.item_key} className="video-player" controls src={currentItem.video_uri} />
                  </div>

                  <div className="stage-footer">
                    <div className="stage-info">
                      <span className="stage-info-label">当前片段</span>
                      <span className="stage-info-value">{currentItem.video_id}.mp4</span>
                    </div>
                    <div className="stage-info">
                      <span className="stage-info-label">修改时间</span>
                      <span className="stage-info-value">{currentItem._modifiedAt ?? '--'}</span>
                    </div>
                  </div>
                </main>
              </div>

              <div className="navigator-card">
                <div className="navigator-toolbar">
                  <div className="panel-actions nav-actions">
                    <button className="btn danger slim" onClick={() => void handleDelete()} disabled={busyAction !== ''}>
                      删除
                    </button>
                    <button className="btn success slim" onClick={() => void handlePass()} disabled={busyAction !== ''}>
                      通过
                    </button>
                    <button
                      className="btn primary slim"
                      onClick={() => void handleModify()}
                      disabled={busyAction !== '' || !isEditingAnswer || !canModify}
                    >
                      确认修改
                    </button>
                  </div>
                  <div className="navigator-header">
                    <div className="panel-title">
                      <span className="panel-title-mark" />
                      题目导航
                    </div>
                    <div className="navigator-text">颜色表示审核状态，点击编号可切换条目。</div>
                  </div>
                </div>

                <div className="navigator-grid">
                  {filteredItems.map((item, idx) => (
                    <button
                      key={item.id}
                      className={`nav-tile nav-${item.status} ${idx === currentIndex ? 'active' : ''}`}
                      onClick={() => setCurrentIndex(idx)}
                      title={`${item.video_id} / ${item.q_category}`}
                    >
                      <span className="nav-index">{idx + 1}</span>
                    </button>
                  ))}
                </div>
              </div>
            </>
          ) : (
            <div className="empty-state">
              <div className="empty-title">暂无可审核条目</div>
              <div className="empty-text">请检查筛选条件，或点击“同步最新数据”重新加载动态问题池。</div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default App
