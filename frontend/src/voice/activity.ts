export type VoiceActivityDecision = 'continue' | 'submit' | 'discard'

export interface VoiceActivityOptions {
  threshold?: number
  silenceMs?: number
  maxTurnMs?: number
}

export interface VoiceActivityTracker {
  update(level: number, elapsedMs: number): VoiceActivityDecision
  reset(): void
}

const DEFAULT_THRESHOLD = 0.035
const DEFAULT_SILENCE_MS = 900
const DEFAULT_MAX_TURN_MS = 15_000

export function createVoiceActivityTracker(options: VoiceActivityOptions = {}): VoiceActivityTracker {
  const threshold = options.threshold ?? DEFAULT_THRESHOLD
  const silenceMs = options.silenceMs ?? DEFAULT_SILENCE_MS
  const maxTurnMs = options.maxTurnMs ?? DEFAULT_MAX_TURN_MS
  let heardSpeech = false
  let lastSpeechAt = 0

  return {
    update(level, elapsedMs) {
      if (level >= threshold) {
        heardSpeech = true
        lastSpeechAt = elapsedMs
      }

      if (elapsedMs >= maxTurnMs) return heardSpeech ? 'submit' : 'discard'
      if (heardSpeech && elapsedMs - lastSpeechAt >= silenceMs) return 'submit'
      return 'continue'
    },
    reset() {
      heardSpeech = false
      lastSpeechAt = 0
    },
  }
}
