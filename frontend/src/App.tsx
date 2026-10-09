import { useCallback, useEffect, useState } from 'react'
import { ApiError, api } from './api/client'
import { CloudConsentDialog } from './components/CloudConsentDialog'
import { ImmersiveChat } from './components/ImmersiveChat'
import { MemoryReview } from './components/MemoryReview'
import { PrivacyControls } from './components/PrivacyControls'
import { StatusBar } from './components/StatusBar'
import { TodayPage } from './components/TodayPage'
import { useSpeechPlayback } from './hooks/useSpeechPlayback'
import { useVoiceSession } from './hooks/useVoiceSession'
import type { CloudPreview, Conversation, HealthState, Page, Route } from './types'

const navigation: Array<{ id: Page; label: string }> = [
  { id: 'chat', label: '对话' }, { id: 'today', label: '今日' }, { id: 'memory', label: '记忆' }, { id: 'relationship', label: '关系设定' }, { id: 'privacy', label: '隐私与云端' },
]

export default function App() {
  const [page, setPage] = useState<Page>('chat')
  const [conversation, setConversation] = useState<Conversation | null>(null)
  const [draft, setDraft] = useState('')
  const [route, setRoute] = useState<Route>('local')
  const [preview, setPreview] = useState<CloudPreview | null>(null)
  const [previewOpen, setPreviewOpen] = useState(false)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const [health, setHealth] = useState<HealthState | null>(null)
  const speech = useSpeechPlayback()

  useEffect(() => {
    void (async () => {
      try { setHealth(await api.health()) } catch { setError('无法连接到本地服务。请确认后端正在 `127.0.0.1` 上运行。') }
      try { const created = await api.createConversation(); setConversation({ ...created, messages: [] }) } catch { /* health message above is sufficient */ }
    })()
  }, [])

  const submitContent = useCallback(async (content: string, submitRoute: Route, cloudPreview?: CloudPreview) => {
    const cleanContent = content.trim()
    if (!cleanContent || sending) return
    if (cleanContent.length > 8000) { setError('这条消息太长，请拆分后发送。'); return }
    if (!conversation) { setError('本地会话尚未准备好，请稍后重试。'); return }

    const optimisticMessageId = `pending-${crypto.randomUUID()}`
    setConversation((current) => current && current.id === conversation.id ? {
      ...current,
      messages: [...current.messages, {
        id: optimisticMessageId,
        role: 'user',
        content: cleanContent,
        route: submitRoute,
        status: 'completed',
        created_at: new Date().toISOString(),
      }],
    } : current)
    setSending(true)
    setError(null)
    try {
      await api.sendMessage(conversation.id, {
        content: cleanContent,
        route: submitRoute,
        request_id: cloudPreview?.request_id,
        cloud_consent_id: cloudPreview?.consent_id,
      })
      const messages = await api.getMessages(conversation.id)
      setConversation((current) => current ? { ...current, messages } : current)
      setDraft('')
      setRoute('local')
      setPreviewOpen(false)
      setPreview(null)

      const latestAssistant = [...messages].reverse().find((message) => message.role === 'assistant')
      if (latestAssistant && speech.autoRead) await speech.play(latestAssistant)
    } catch (requestError) {
      setConversation((current) => current ? {
        ...current,
        messages: current.messages.filter((message) => message.id !== optimisticMessageId),
      } : current)
      setError(messageFor(requestError))
    } finally {
      setSending(false)
    }
  }, [conversation, sending, speech.autoRead, speech.play])

  const handleVoiceTranscript = useCallback(async (text: string) => {
    await submitContent(text, 'local')
  }, [submitContent])

  const voiceSuspended = sending || speech.loadingMessageId !== null || speech.activeMessageId !== null
  const voice = useVoiceSession({ suspended: voiceSuspended, onTranscript: handleVoiceTranscript })

  useEffect(() => {
    if (!voice.active) return
    if (speech.loadingMessageId !== null || speech.activeMessageId !== null) voice.setPhase('speaking')
    else if (sending) voice.setPhase('thinking')
  }, [sending, speech.activeMessageId, speech.loadingMessageId, voice.active, voice.setPhase])

  const beginSend = async () => {
    const cleanDraft = draft.trim()
    if (!cleanDraft || sending) return
    if (cleanDraft.length > 8000) { setError('这条消息太长，请拆分后发送。'); return }
    if (!conversation) { setError('本地会话尚未准备好，请稍后重试。'); return }
    speech.prepareForPlayback()
    setError(null)
    if (route === 'local') { await submitContent(cleanDraft, 'local'); return }

    setPreviewOpen(true)
    setPreview(null)
    setPreviewLoading(true)
    try { setPreview(await api.createCloudConsent(cleanDraft, route)) }
    catch (requestError) { setError(messageFor(requestError)) }
    finally { setPreviewLoading(false) }
  }

  const stopVoice = () => {
    voice.stop()
    speech.stop()
  }

  const startVoice = () => {
    speech.prepareForPlayback()
    void voice.start()
  }

  const navigateFromChat = (nextPage: Page) => {
    if (nextPage !== 'chat') stopVoice()
    setPage(nextPage)
  }

  const currentView = page === 'chat'
    ? <ImmersiveChat
      conversation={conversation}
      draft={draft}
      route={route}
      sending={sending}
      health={health}
      error={error}
      voiceNotice={voice.error ?? speech.voiceNotice}
      voiceActive={voice.active}
      voicePhase={voice.phase}
      autoRead={speech.autoRead}
      activeMessageId={speech.activeMessageId}
      loadingMessageId={speech.loadingMessageId}
      onDraftChange={setDraft}
      onRouteChange={setRoute}
      onSubmit={() => void beginSend()}
      onNavigate={navigateFromChat}
      onStartVoice={startVoice}
      onStopVoice={stopVoice}
      onAutoReadChange={speech.setAutoRead}
      onPlayMessage={(message) => { speech.prepareForPlayback(); void speech.play(message) }}
    />
    : <div className="app-shell">
      <aside className="navigation"><div className="brand"><span>◐</span><div><strong>栖语</strong><small>本地优先</small></div></div><nav aria-label="主导航">{navigation.map((item) => <button key={item.id} className={page === item.id ? 'nav-active' : ''} onClick={() => setPage(item.id)}>{item.label}</button>)}</nav><div className="sidebar-foot">所有数据默认只在此设备保存</div></aside>
      <main>
        <StatusBar route={route} health={health} />
        {page === 'memory' && <MemoryReview conversationId={conversation?.id} />}
        {page === 'privacy' && <PrivacyControls />}
        {page === 'today' && <TodayPage conversationId={conversation?.id} />}
        {page === 'relationship' && <Placeholder title="关系设定" body="温柔知性型人格已启用。称呼、说话长度、互动频率与禁忌话题会在本机设置中开放。" />}
      </main>
    </div>

  return <>
    {currentView}
    {previewOpen && <CloudConsentDialog
      preview={preview}
      loading={previewLoading}
      error={error}
      onCancel={() => { setPreviewOpen(false); setPreview(null) }}
      onApprove={() => void submitContent(draft, route, preview ?? undefined)}
    />}
  </>
}

function Placeholder({ title, body }: { title: string; body: string }) {
  return <section className="page-content"><div className="page-heading"><div><p className="eyebrow">本地控制台</p><h1>{title}</h1></div></div><div className="empty-state"><h2>首版界面已预留</h2><p>{body}</p></div></section>
}

function messageFor(error: unknown) {
  return error instanceof ApiError ? error.message : '发生了未预期的本地错误。内容没有上传。'
}
