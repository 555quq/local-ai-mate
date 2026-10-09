import { describe, expect, it } from 'vitest'
import { createVoiceActivityTracker } from './activity'

describe('createVoiceActivityTracker', () => {
  it('discards a turn that stays below the speech threshold', () => {
    const tracker = createVoiceActivityTracker({ threshold: 0.05, silenceMs: 900, maxTurnMs: 15_000 })

    expect(tracker.update(0.01, 0)).toBe('continue')
    expect(tracker.update(0.02, 14_999)).toBe('continue')
    expect(tracker.update(0.01, 15_000)).toBe('discard')
  })

  it('submits after detected speech is followed by 900ms of silence', () => {
    const tracker = createVoiceActivityTracker({ threshold: 0.05, silenceMs: 900, maxTurnMs: 15_000 })

    expect(tracker.update(0.08, 100)).toBe('continue')
    expect(tracker.update(0.01, 999)).toBe('continue')
    expect(tracker.update(0.01, 1_000)).toBe('submit')
  })

  it('submits voiced audio at the hard turn limit', () => {
    const tracker = createVoiceActivityTracker({ threshold: 0.05, silenceMs: 900, maxTurnMs: 15_000 })

    expect(tracker.update(0.08, 14_999)).toBe('continue')
    expect(tracker.update(0.08, 15_000)).toBe('submit')
  })

  it('discards unvoiced audio at the hard turn limit', () => {
    const tracker = createVoiceActivityTracker({ threshold: 0.05, silenceMs: 900, maxTurnMs: 15_000 })

    expect(tracker.update(0.01, 15_000)).toBe('discard')
  })
})
