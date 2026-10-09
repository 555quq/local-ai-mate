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

export type NicknameFrequency = 'never' | 'rare' | 'natural' | 'frequent'
export type PersonalityStyle = 'gentle-intellectual' | 'cool-restrained' | 'bright-playful' | 'calm-supportive'
export type IntimacyLevel = 'restrained' | 'natural' | 'close'
export type ReplyLength = 'short' | 'medium' | 'detailed'
export type QuestionFrequency = 'low' | 'medium' | 'high'
export type ComfortStyle = 'listen-first' | 'balanced' | 'advice-first'

export interface RelationshipSettings {
  companion_name: string
  self_reference: string
  user_display_name: string
  user_nickname: string
  nickname_frequency: NicknameFrequency
  personality_style: PersonalityStyle
  intimacy_level: IntimacyLevel
  reply_length: ReplyLength
  question_frequency: QuestionFrequency
  comfort_style: ComfortStyle
  opening_greeting: string
  blocked_terms: string[]
  version: number
  updated_at: string
}

export const DEFAULT_RELATIONSHIP_SETTINGS: RelationshipSettings = {
  companion_name: '露娜',
  self_reference: '我',
  user_display_name: '',
  user_nickname: '',
  nickname_frequency: 'natural',
  personality_style: 'gentle-intellectual',
  intimacy_level: 'natural',
  reply_length: 'medium',
  question_frequency: 'medium',
  comfort_style: 'balanced',
  opening_greeting: '',
  blocked_terms: [],
  version: 1,
  updated_at: '',
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
