export type Route = 'local' | 'cloud-current-message' | 'cloud-with-summary'
export type Page = 'chat' | 'today' | 'memory' | 'relationship' | 'privacy'
export type VoicePhase = 'idle' | 'requesting' | 'listening' | 'recording' | 'transcribing' | 'thinking' | 'speaking' | 'error'

export interface SpeechTranscript {
  text: string
  language: string
  duration_ms: number
}

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  route: Route
  status: 'completed' | 'streaming' | 'stopped' | 'failed' | 'degraded'
  created_at: string
}

export interface Conversation {
  id: string
  title: string
  messages: Message[]
}

export interface CloudPreview {
  request_id: string
  consent_id: string
  expires_at: string
  provider_id: string
  mode: Extract<Route, 'cloud-current-message' | 'cloud-with-summary'>
  fields: Array<{ name: string; character_count: number }>
}

export interface MemoryCandidate {
  id: string
  content: string
  category: 'profile' | 'preference' | 'event' | 'plan'
  source_message_id: string
  private_local_only: boolean
}

export interface ReminderProposal {
  id: string
  title: string
  absolute_due_at: string
  timezone: string
  repeat_rule?: string | null
}

export interface PrivacySettings {
  memory_candidates_paused: boolean
  retrieval_paused: boolean
  proactive_interactions_paused: boolean
}

export interface LocalSettingsResponse {
  privacy: PrivacySettings
  model: Record<string, unknown>
  cloud_outbound_enabled: boolean
}

export interface HealthState {
  database: string
  model: string | OllamaStatus
  cloud_outbound_enabled: boolean
  scheduler?: string
  ollama?: {
    available: boolean
    model?: string
    detail?: string
  }
  tts?: {
    enabled: boolean
    status: 'configured' | 'warming' | 'ready' | 'degraded' | 'unavailable' | 'failed'
    primary_engine?: string
    device?: 'cuda' | 'cpu' | null
    fallback_ready?: boolean
    last_error?: string | null
  }
  stt?: {
    enabled: boolean
    status: 'configured' | 'ready' | 'unavailable' | 'failed'
  }
}

export interface OllamaStatus {
  enabled: boolean
  status: string
  model?: string
  endpoint?: string | null
  reason?: string
}

export interface Reminder {
  id: string
  title: string
  trigger_at: string
  timezone: string
  repeat_rule: string | null
}

export interface ApiErrorShape {
  code: string
  message: string
  request_id?: string
  retryable?: boolean
}
