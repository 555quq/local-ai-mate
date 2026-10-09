import type { CloudPreview, Conversation, HealthState, LocalSettingsResponse, MemoryCandidate, PrivacySettings, Reminder, ReminderProposal, Route, SpeechTranscript } from '../types'

const BASE_URL = (import.meta.env.VITE_API_BASE ?? '/api/v1').replace(/\/$/, '')
const SESSION_TOKEN = import.meta.env.VITE_SESSION_TOKEN

export class ApiError extends Error {
  constructor(public readonly code: string, message: string, public readonly retryable = false) { super(message) }
}

interface Envelope<T> { request_id: string; server_time: string; data: T }
interface Problem { error?: { code?: string; message?: string; retryable?: boolean } }

export type SpeechStreamEvent =
  | { event: 'started'; requestId: string; engine: string; device: string }
  | { event: 'reset'; requestId: string; engine: string; device: string; reason: string }
  | { event: 'fragment'; requestId: string; index: number; audio: Blob }
  | { event: 'completed'; requestId: string; fragmentCount: number; generationSeconds: number }

function writeHeaders(): HeadersInit {
  // The token only authenticates the loopback API. It is never a cloud credential.
  // Some read-only privacy endpoints also require it, so apply it consistently.
  return { 'Content-Type': 'application/json', ...(SESSION_TOKEN ? { 'X-Session-Token': SESSION_TOKEN } : {}) }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, { ...options, headers: { ...writeHeaders(), ...options.headers }, credentials: 'same-origin' })
  const body = await response.json().catch(() => null) as Envelope<T> | Problem | null
  if (!response.ok) {
    const error = (body as Problem | null)?.error
    throw new ApiError(error?.code ?? `HTTP_${response.status}`, error?.message ?? '本地服务暂时不可用。', error?.retryable ?? false)
  }
  return (body as Envelope<T>).data
}

async function requestAudio(path: string, options: RequestInit): Promise<Blob> {
  const response = await fetch(`${BASE_URL}${path}`, { ...options, headers: { ...writeHeaders(), ...options.headers }, credentials: 'same-origin' })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as Problem | null
    const error = body?.error
    throw new ApiError(error?.code ?? `HTTP_${response.status}`, error?.message ?? '本地语音服务暂时不可用。', error?.retryable ?? false)
  }
  return response.blob()
}

function decodeWavBase64(value: unknown): Blob {
  if (typeof value !== 'string' || value.length === 0 || value.length > 6 * 1024 * 1024) {
    throw new ApiError('TTS_STREAM_INVALID', '本地语音流包含无效音频。', true)
  }
  try {
    const decoded = atob(value)
    const bytes = new Uint8Array(decoded.length)
    for (let index = 0; index < decoded.length; index += 1) bytes[index] = decoded.charCodeAt(index)
    return new Blob([bytes], { type: 'audio/wav' })
  } catch {
    throw new ApiError('TTS_STREAM_INVALID', '本地语音流包含无效音频。', true)
  }
}

async function streamSpeech(
  text: string,
  signal: AbortSignal,
  onEvent: (event: SpeechStreamEvent) => void,
): Promise<void> {
  const response = await fetch(`${BASE_URL}/tts/stream`, {
    method: 'POST',
    headers: { ...writeHeaders(), Accept: 'application/x-ndjson' },
    credentials: 'same-origin',
    body: JSON.stringify({ text }),
    signal,
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as Problem | null
    const error = body?.error
    throw new ApiError(error?.code ?? `HTTP_${response.status}`, error?.message ?? '本地语音服务暂时不可用。', error?.retryable ?? false)
  }
  if (!response.body) throw new ApiError('TTS_STREAM_UNAVAILABLE', '本地语音流不可用。', true)

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let requestId: string | null = null
  let expectedIndex = 0
  let started = false
  let completed = false

  const consume = (line: string) => {
    if (!line.trim()) return
    let payload: Record<string, unknown>
    try { payload = JSON.parse(line) as Record<string, unknown> } catch {
      throw new ApiError('TTS_STREAM_INVALID', '本地语音流格式无效。', true)
    }
    const event = payload.event
    const incomingRequestId = payload.request_id
    if (typeof incomingRequestId !== 'string' || (requestId !== null && incomingRequestId !== requestId)) {
      throw new ApiError('TTS_STREAM_INVALID', '本地语音流请求标识无效。', true)
    }
    requestId = incomingRequestId
    if (event === 'started') {
      if (started || typeof payload.engine !== 'string' || typeof payload.device !== 'string') {
        throw new ApiError('TTS_STREAM_INVALID', '本地语音流起始事件无效。', true)
      }
      started = true
      onEvent({ event, requestId, engine: payload.engine, device: payload.device })
      return
    }
    if (!started) throw new ApiError('TTS_STREAM_INVALID', '本地语音流事件顺序无效。', true)
    if (event === 'reset') {
      if (typeof payload.engine !== 'string' || typeof payload.device !== 'string' || payload.reason !== 'primary_failed') {
        throw new ApiError('TTS_STREAM_INVALID', '本地语音流恢复事件无效。', true)
      }
      expectedIndex = 0
      onEvent({ event, requestId, engine: payload.engine, device: payload.device, reason: payload.reason })
      return
    }
    if (event === 'fragment') {
      if (payload.index !== expectedIndex || payload.media_type !== 'audio/wav') {
        throw new ApiError('TTS_STREAM_INVALID', '本地语音片段顺序无效。', true)
      }
      onEvent({ event, requestId, index: expectedIndex, audio: decodeWavBase64(payload.audio_base64) })
      expectedIndex += 1
      return
    }
    if (event === 'completed') {
      if (payload.fragment_count !== expectedIndex || expectedIndex === 0) {
        throw new ApiError('TTS_STREAM_INVALID', '本地语音流结束事件无效。', true)
      }
      completed = true
      onEvent({
        event,
        requestId,
        fragmentCount: expectedIndex,
        generationSeconds: typeof payload.generation_seconds === 'number' ? payload.generation_seconds : 0,
      })
      return
    }
    if (event === 'error') {
      throw new ApiError(
        typeof payload.code === 'string' ? payload.code : 'TTS_STREAM_INTERRUPTED',
        '语音生成中断，文字回复不受影响。',
        payload.retryable === true,
      )
    }
    throw new ApiError('TTS_STREAM_INVALID', '本地语音流包含未知事件。', true)
  }

  while (true) {
    const chunk = await reader.read()
    if (chunk.done) break
    buffer += decoder.decode(chunk.value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''
    for (const line of lines) consume(line)
  }
  buffer += decoder.decode()
  if (buffer.trim()) consume(buffer)
  if (!completed) throw new ApiError('TTS_STREAM_INTERRUPTED', '语音生成中断，文字回复不受影响。', true)
}

/** A missing optional endpoint is a capability gap, not a successful action. */
async function optionalRequest<T>(path: string, options: RequestInit = {}): Promise<T | undefined> {
  try { return await request<T>(path, options) } catch (error) {
    if (error instanceof ApiError && error.code === 'HTTP_404') return undefined
    throw error
  }
}

function uuid() { return crypto.randomUUID() }

async function sendMessageStream(conversationId: string, input: { content: string; route: Route; request_id?: string; cloud_consent_id?: string }): Promise<void> {
  const response = await fetch(`${BASE_URL}/conversations/${encodeURIComponent(conversationId)}/messages`, {
    method: 'POST', headers: { ...writeHeaders(), Accept: 'text/event-stream' }, credentials: 'same-origin',
    body: JSON.stringify({ ...input, idempotency_key: uuid() }),
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as Problem | null
    const error = body?.error
    throw new ApiError(error?.code ?? `HTTP_${response.status}`, error?.message ?? '本地服务暂时不可用。', error?.retryable ?? false)
  }
  if (!response.body) throw new ApiError('STREAM_UNAVAILABLE', '本地回复流不可用。', true)
  const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = ''; let finished = false
  while (!finished) {
    const chunk = await reader.read(); if (chunk.done) break
    buffer += decoder.decode(chunk.value, { stream: true })
    const frames = buffer.split('\n\n'); buffer = frames.pop() ?? ''
    for (const frame of frames) {
      const dataLine = frame.split('\n').find((line) => line.startsWith('data: '))
      if (!dataLine) continue
      const event = JSON.parse(dataLine.slice(6)) as { event: string }
      if (event.event === 'completed' || event.event === 'degraded') finished = true
    }
  }
}

export const api = {
  health: () => request<HealthState>('/health'),
  createConversation: () => request<{ id: string; title: string }>('/conversations', { method: 'POST', body: JSON.stringify({ title: '新对话', route_default: 'local', idempotency_key: uuid() }) }),
  getMessages: async (id: string): Promise<Conversation['messages']> => {
    const result = await request<{ items: Array<{ id: string; role: 'user' | 'assistant'; content: string; source: string | null; status: Conversation['messages'][number]['status']; created_at: string }> }>(`/conversations/${encodeURIComponent(id)}/messages`)
    return result.items.map((message) => ({ ...message, route: message.source === 'fallback-local' ? 'cloud-current-message' : 'local' }))
  },
  sendMessage: sendMessageStream,
  synthesizeSpeech: (text: string) => requestAudio('/tts/synthesize', { method: 'POST', body: JSON.stringify({ text }) }),
  streamSpeech,
  transcribeSpeech: (audio: Blob) => request<SpeechTranscript>('/stt/transcribe', {
    method: 'POST',
    headers: { 'Content-Type': audio.type },
    body: audio,
  }),
  createCloudConsent: async (content: string, mode: Extract<Route, 'cloud-current-message' | 'cloud-with-summary'>): Promise<CloudPreview> => {
    const request_id = uuid(); const upload_mode = mode === 'cloud-current-message' ? 'current-message' : 'cloud-with-summary'
    const result = await request<{ consent_id: string; expires_at: string; provider_id: string; upload_fields: string[] }>('/cloud/consents', { method: 'POST', body: JSON.stringify({ request_id, provider_id: 'configured-provider', upload_mode, idempotency_key: uuid() }) })
    return { request_id, consent_id: result.consent_id, expires_at: result.expires_at, provider_id: result.provider_id, mode, fields: result.upload_fields.map((name) => ({ name, character_count: name === 'current_message' ? content.length : 0 })) }
  },
  listMemoryCandidates: (conversationId: string) => request<{ items: MemoryCandidate[] }>(`/conversations/${encodeURIComponent(conversationId)}/memory-candidates`).then((result) => result.items),
  approveMemoryCandidate: (item: MemoryCandidate, private_local_only: boolean) => request<void>(`/memory-candidates/${encodeURIComponent(item.id)}/approve`, { method: 'POST', body: JSON.stringify({ content: item.content, category: item.category, importance: 3, private_local_only, idempotency_key: uuid() }) }),
  createReminderProposal: (input: { conversation_id?: string; title: string; absolute_due_at: string; timezone: string; source_text: string }) => request<ReminderProposal>('/action-proposals', { method: 'POST', body: JSON.stringify({ ...input, kind: 'reminder', repeat_rule: null, idempotency_key: uuid() }) }),
  confirmReminder: (id: string, replace_existing: boolean) => request<void>(`/action-proposals/${encodeURIComponent(id)}/confirm`, { method: 'POST', body: JSON.stringify({ replace_existing, idempotency_key: uuid() }) }),
  listReminders: () => request<{ items: Reminder[] }>('/reminders').then((result) => result.items),
  getPrivacySettings: async () => {
    const result = await optionalRequest<LocalSettingsResponse>('/settings')
    return result?.privacy
  },
  updatePrivacySettings: async (settings: Partial<PrivacySettings>) => {
    const result = await optionalRequest<LocalSettingsResponse>('/settings', { method: 'PATCH', body: JSON.stringify({ ...settings, idempotency_key: uuid() }) })
    return result?.privacy
  },
  exportPrivacyData: () => optionalRequest<unknown>('/privacy/export'),
  clearPrivacyData: () => optionalRequest<void>('/privacy/clear', { method: 'POST', body: JSON.stringify({ confirmation: '清空本地数据', confirmed: true, idempotency_key: uuid() }) }),
}
