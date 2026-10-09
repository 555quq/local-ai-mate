import { useEffect, useMemo, useRef, useState } from 'react'
import lunaBackground from '../assets/luna-background.png'
import type { Conversation, HealthState, Message, Page, Route } from '../types'

export type ImmersiveVoicePhase =
  | 'idle'
  | 'requesting'
  | 'listening'
  | 'recording'
  | 'transcribing'
  | 'thinking'
  | 'speaking'
  | 'error'

export interface ImmersiveChatProps {
  conversation: Conversation | null
  draft: string
  route: Route
  sending: boolean
  health: HealthState | null
  error?: string | null
  voiceNotice?: string | null
  voiceActive: boolean
  voicePhase: ImmersiveVoicePhase
  autoRead: boolean
  activeMessageId: string | null
  loadingMessageId: string | null
  onDraftChange: (value: string) => void
  onRouteChange: (route: Route) => void
  onSubmit: () => void
  onNavigate: (page: Page) => void
  onStartVoice: () => void
  onStopVoice: () => void
  onAutoReadChange: (enabled: boolean) => void
  onPlayMessage: (message: Message) => void
}

const phaseLabels: Record<ImmersiveVoicePhase, string> = {
  idle: '待命',
  requesting: '正在请求麦克风权限',
  listening: '正在聆听',
  recording: '正在听你说',
  transcribing: '正在本地转写',
  thinking: '露娜正在思考',
  speaking: '露娜正在说话',
  error: '语音暂不可用',
}

const pageButtons: Array<{ page: Exclude<Page, 'chat' | 'relationship'>; label: string; icon: IconName }> = [
  { page: 'today', label: '打开今日', icon: 'calendar' },
  { page: 'memory', label: '打开记忆', icon: 'memory' },
  { page: 'privacy', label: '打开设置', icon: 'settings' },
]

type IconName = 'history' | 'calendar' | 'memory' | 'settings' | 'close' | 'play' | 'stop' | 'send'

export function ImmersiveChat(props: ImmersiveChatProps) {
  const {
    conversation, draft, route, sending, health, error, voiceNotice, voiceActive,
    voicePhase, autoRead, activeMessageId, loadingMessageId, onDraftChange,
    onRouteChange, onSubmit, onNavigate, onStartVoice, onStopVoice,
    onAutoReadChange, onPlayMessage,
  } = props
  const [historyOpen, setHistoryOpen] = useState(false)
  const historyButtonRef = useRef<HTMLButtonElement>(null)
  const closeButtonRef = useRef<HTMLButtonElement>(null)

  const messages = conversation?.messages ?? []
  const latestUser = useMemo(() => findLatest(messages, 'user'), [messages])
  const latestAssistant = useMemo(() => findLatest(messages, 'assistant'), [messages])
  const subtitle = useRotatingSubtitle(latestAssistant?.content ?? '')

  useEffect(() => {
    if (!historyOpen) return
    closeButtonRef.current?.focus()
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      setHistoryOpen(false)
      historyButtonRef.current?.focus()
    }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [historyOpen])

  const closeHistory = () => {
    historyButtonRef.current?.focus()
    setHistoryOpen(false)
  }

  const conversationReady = Boolean(conversation)
  const voiceLabel = phaseLabels[voicePhase]
  const modelReady = isModelReady(health)
  const ttsReady = health?.tts?.status === 'configured' || health?.tts?.status === 'ready' || health?.tts?.status === 'warming' || health?.tts?.status === 'degraded'

  return <section className="immersive-chat" aria-label="露娜沉浸式对话">
    <img className="immersive-backdrop" src={lunaBackground} alt="" aria-hidden="true" />
    <div className="immersive-highlight" aria-hidden="true" />

    <header className="immersive-tools" aria-label="对话工具">
      <button ref={historyButtonRef} className="immersive-icon-button" type="button" aria-label="打开对话历史" title="对话历史" aria-expanded={historyOpen} onClick={() => setHistoryOpen(true)}><Icon name="history" /></button>
      {pageButtons.map((item) => <button key={item.page} className="immersive-icon-button" type="button" aria-label={item.label} title={item.label.replace('打开', '')} onClick={() => onNavigate(item.page)}><Icon name={item.icon} /></button>)}
    </header>

    <div className="immersive-local-state" aria-label="本地服务状态">
      <span><i className={`immersive-state-dot ${modelReady ? 'ready' : ''}`} />{modelReady ? '本地模型' : '本地模型未就绪'}</span>
      <span><i className={`immersive-state-dot ${ttsReady ? 'ready' : ''}`} />{ttsReady ? '本地语音' : '文字模式'}</span>
    </div>

    <div className={`immersive-voice immersive-voice--${voicePhase}`}>
      <button
        className="immersive-orb"
        type="button"
        aria-label={voiceActive ? `连续语音会话：${voiceLabel}` : '开始连续语音会话'}
        aria-pressed={voiceActive}
        disabled={voiceActive}
        onClick={onStartVoice}
      ><span aria-hidden="true" /></button>
      <p className="immersive-voice-label" role="status" aria-live="polite">{voiceLabel}</p>
      {voiceActive && <button className="immersive-stop" type="button" aria-label="停止连续语音会话" title="停止连续语音会话" onClick={onStopVoice}><Icon name="stop" />停止</button>}
    </div>

    <div className="immersive-subtitles" aria-label="当前字幕" aria-live="polite" aria-atomic="true">
      {latestUser && <p className="immersive-user-subtitle"><span>你</span>{latestUser.content}</p>}
      {subtitle && <p className="immersive-assistant-subtitle"><span>露娜</span>{subtitle}</p>}
    </div>

    <div className="immersive-composer-wrap">
      {(voiceNotice || error) && <p className={error ? 'immersive-alert' : 'immersive-notice'} role={error ? 'alert' : 'status'}>{error ?? voiceNotice}</p>}
      <div className="immersive-composer">
        <textarea
          value={draft}
          maxLength={8000}
          rows={1}
          disabled={sending || !conversationReady}
          onChange={(event) => onDraftChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault()
              onSubmit()
            }
          }}
          placeholder={conversationReady ? '想和露娜说点什么？' : '正在准备本地会话…'}
          aria-label="消息内容"
        />
        <label className="immersive-route"><span className="sr-only">回答方式</span><select aria-label="回答方式" value={route} onChange={(event) => onRouteChange(event.target.value as Route)} disabled={sending}>
          <option value="local">本地</option>
          <option value="cloud-current-message">云端 · 当前消息</option>
          <option value="cloud-with-summary">云端 · 临时摘要</option>
        </select></label>
        <button className="immersive-send" type="button" aria-label="发送消息" title="发送消息" disabled={sending || !draft.trim() || !conversationReady} onClick={onSubmit}><Icon name="send" /></button>
      </div>
      <label className="immersive-auto-read"><input type="checkbox" checked={autoRead} onChange={(event) => onAutoReadChange(event.target.checked)} /><span aria-hidden="true" />自动朗读露娜的回复</label>
    </div>

    {historyOpen && <div className="immersive-history-layer" data-testid="history-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) closeHistory() }}>
      <aside className="immersive-history" role="dialog" aria-modal="false" aria-label="对话历史">
        <div className="immersive-history-heading"><div><p>只保存在此设备</p><h2>{conversation?.title || '对话历史'}</h2></div><button ref={closeButtonRef} className="immersive-icon-button" type="button" aria-label="关闭对话历史" title="关闭" onClick={closeHistory}><Icon name="close" /></button></div>
        <div className="immersive-history-messages" aria-live="polite">
          {!conversation && <p className="immersive-history-empty">正在准备本地会话…</p>}
          {conversation && messages.length === 0 && <p className="immersive-history-empty">还没有消息。说点什么吧。</p>}
          {messages.map((message) => <article className={`immersive-history-message immersive-history-message--${message.role}`} key={message.id}>
            <div className="immersive-history-meta"><span>{message.role === 'assistant' ? '露娜' : '你'}</span><span>{message.route === 'local' ? '本地' : '云端增强'}</span>{message.status !== 'completed' && <span>{statusLabel(message.status)}</span>}</div>
            <p>{message.content}</p>
            {message.role === 'assistant' && <button className="immersive-history-play" type="button" aria-label={`${activeMessageId === message.id ? '停止朗读' : '朗读'}露娜的这条回复`} aria-pressed={activeMessageId === message.id} disabled={loadingMessageId !== null && loadingMessageId !== message.id} onClick={() => onPlayMessage(message)}><Icon name={activeMessageId === message.id ? 'stop' : 'play'} />{activeMessageId === message.id ? '停止' : loadingMessageId === message.id ? '准备中' : '朗读'}</button>}
          </article>)}
        </div>
      </aside>
    </div>}
  </section>
}

function findLatest(messages: Message[], role: Message['role']) {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index].role === role) return messages[index]
  }
  return undefined
}

function useRotatingSubtitle(content: string) {
  const sentences = useMemo(() => splitSubtitle(content), [content])
  const [index, setIndex] = useState(0)

  useEffect(() => setIndex(0), [content])
  useEffect(() => {
    if (sentences.length < 2 || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    const timer = window.setInterval(() => setIndex((current) => (current + 1) % sentences.length), 4200)
    return () => window.clearInterval(timer)
  }, [sentences])

  return sentences[index] ?? ''
}

function splitSubtitle(content: string) {
  const parts = content.trim().match(/[^。！？!?\n]+[。！？!?]?/g)?.map((part) => part.trim()).filter(Boolean) ?? []
  if (parts.length < 2) return parts
  const chunks: string[] = []
  let chunk = ''
  for (const sentence of parts) {
    if (chunk && chunk.length + sentence.length > 72) {
      chunks.push(chunk)
      chunk = sentence
    } else {
      chunk += sentence
    }
  }
  if (chunk) chunks.push(chunk)
  return chunks
}

function isModelReady(health: HealthState | null) {
  if (!health) return false
  if (typeof health.model === 'string') return health.model === 'mock-local-ready' || health.model === 'ollama-ready' || health.model.startsWith('ollama-')
  return health.model.status === 'ready'
}

function statusLabel(status: Message['status']) {
  if (status === 'stopped') return '已停止'
  if (status === 'streaming') return '生成中'
  if (status === 'failed') return '失败'
  if (status === 'degraded') return '已降级'
  return ''
}

function Icon({ name }: { name: IconName }) {
  const paths: Record<IconName, React.ReactNode> = {
    history: <><path d="M3 12a9 9 0 1 0 3-6.7" /><path d="M3 4v5h5M12 7v5l3 2" /></>,
    calendar: <><rect x="3" y="5" width="18" height="16" rx="2" /><path d="M16 3v4M8 3v4M3 10h18" /></>,
    memory: <><path d="M9 4a3 3 0 0 0-5 2.2A3.5 3.5 0 0 0 5.5 13 3.5 3.5 0 0 0 9 18h2V4Z" /><path d="M15 4a3 3 0 0 1 5 2.2A3.5 3.5 0 0 1 18.5 13a3.5 3.5 0 0 1-3.5 5h-2V4Z" /></>,
    settings: <><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3A1.7 1.7 0 0 0 10 3v-.2h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z" /></>,
    close: <path d="m6 6 12 12M18 6 6 18" />,
    play: <path d="m9 7 8 5-8 5V7Z" />,
    stop: <rect x="8" y="8" width="8" height="8" rx="1" />,
    send: <><path d="m4 4 17 8-17 8 3-8-3-8Z" /><path d="M7 12h14" /></>,
  }
  return <svg className="immersive-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">{paths[name]}</svg>
}
