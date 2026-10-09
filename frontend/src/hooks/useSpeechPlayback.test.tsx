import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api } from '../api/client'
import type { Message } from '../types'
import { useSpeechPlayback } from './useSpeechPlayback'

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return { ...actual, api: { ...actual.api, streamSpeech: vi.fn() } }
})

class FakeAudio {
  static instances: FakeAudio[] = []
  static playImplementation: () => Promise<void> = () => Promise.resolve()
  onended: (() => void) | null = null
  onerror: (() => void) | null = null
  currentTime = 0
  muted = false
  volume = 1
  pause = vi.fn()
  removeAttribute = vi.fn()
  load = vi.fn()
  play = vi.fn<() => Promise<void>>(() => FakeAudio.playImplementation())

  constructor(readonly src: string) { FakeAudio.instances.push(this) }
}

const message: Message = {
  id: 'assistant-1', role: 'assistant', content: '你好，我是露娜。', route: 'local', status: 'completed', created_at: '2026-10-08T00:00:00Z',
}

describe('useSpeechPlayback', () => {
  beforeEach(() => {
    FakeAudio.instances = []
    FakeAudio.playImplementation = () => Promise.resolve()
    vi.mocked(api.streamSpeech).mockReset().mockImplementation(async (_text, _signal, onEvent) => {
      onEvent({ event: 'started', requestId: 'request-1', engine: 'gpt-sovits', device: 'cuda' })
      onEvent({ event: 'fragment', requestId: 'request-1', index: 0, audio: new Blob(['wav'], { type: 'audio/wav' }) })
      onEvent({ event: 'completed', requestId: 'request-1', fragmentCount: 1, generationSeconds: 1 })
    })
    vi.stubGlobal('Audio', FakeAudio)
    let objectUrlIndex = 0
    vi.stubGlobal('URL', { createObjectURL: vi.fn(() => `blob:luna-audio-${objectUrlIndex += 1}`), revokeObjectURL: vi.fn() })
  })

  afterEach(() => vi.unstubAllGlobals())

  it('keeps play pending until the audio ended event', async () => {
    const { result } = renderHook(() => useSpeechPlayback())
    let playback!: Promise<string>
    let resolved = false

    await act(async () => {
      playback = result.current.play(message)
      void playback.then(() => { resolved = true })
      await Promise.resolve()
    })
    await waitFor(() => expect(result.current.activeMessageId).toBe(message.id))
    expect(resolved).toBe(false)

    act(() => FakeAudio.instances[0].onended?.())
    await expect(playback).resolves.toBe('ended')
    expect(result.current.activeMessageId).toBeNull()
  })

  it('primes browser media playback from a user gesture', async () => {
    const { result } = renderHook(() => useSpeechPlayback())
    act(() => result.current.prepareForPlayback())
    expect(FakeAudio.instances).toHaveLength(1)
    expect(FakeAudio.instances[0].play).toHaveBeenCalledTimes(1)
    await act(async () => { await Promise.resolve() })
    expect(FakeAudio.instances[0].pause).toHaveBeenCalledTimes(1)
    expect(FakeAudio.instances[0].removeAttribute).toHaveBeenCalledWith('src')
  })

  it('resolves a pending playback as stopped and clears active state', async () => {
    const { result } = renderHook(() => useSpeechPlayback())
    let playback!: Promise<string>
    await act(async () => { playback = result.current.play(message); await Promise.resolve() })
    await waitFor(() => expect(result.current.activeMessageId).toBe(message.id))

    act(() => result.current.stop())

    await expect(playback).resolves.toBe('stopped')
    expect(FakeAudio.instances[0].pause).toHaveBeenCalledTimes(1)
    expect(result.current.activeMessageId).toBeNull()
  })

  it('plays streamed fragments in order without waiting for the full response', async () => {
    vi.mocked(api.streamSpeech).mockImplementation(async (_text, _signal, onEvent) => {
      onEvent({ event: 'started', requestId: 'request-1', engine: 'gpt-sovits', device: 'cuda' })
      onEvent({ event: 'fragment', requestId: 'request-1', index: 0, audio: new Blob(['one']) })
      onEvent({ event: 'fragment', requestId: 'request-1', index: 1, audio: new Blob(['two']) })
      onEvent({ event: 'completed', requestId: 'request-1', fragmentCount: 2, generationSeconds: 2 })
    })
    let playback!: Promise<string>
    const { result } = renderHook(() => useSpeechPlayback())
    await act(async () => { playback = result.current.play(message); await Promise.resolve() })
    await waitFor(() => expect(FakeAudio.instances).toHaveLength(1))
    act(() => FakeAudio.instances[0].onended?.())
    await waitFor(() => expect(FakeAudio.instances).toHaveLength(2))
    act(() => FakeAudio.instances[1].onended?.())
    await expect(playback).resolves.toBe('ended')
    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(2)
  })

  it('reports blocked autoplay without losing the text response', async () => {
    const { result } = renderHook(() => useSpeechPlayback())
    const blocked = new DOMException('blocked', 'NotAllowedError')
    FakeAudio.playImplementation = () => Promise.reject(blocked)

    let outcome: string | undefined
    await act(async () => { outcome = await result.current.play(message) })

    expect(outcome).toBe('blocked')
    expect(result.current.activeMessageId).toBeNull()
    expect(result.current.voiceNotice).toMatch(/浏览器阻止/)
  })

  it('reports an audio element error and settles the waiter', async () => {
    const { result } = renderHook(() => useSpeechPlayback())
    let playback!: Promise<string>
    await act(async () => { playback = result.current.play(message); await Promise.resolve() })
    await waitFor(() => expect(result.current.activeMessageId).toBe(message.id))

    act(() => FakeAudio.instances[0].onerror?.())

    await expect(playback).resolves.toBe('failed')
    expect(result.current.activeMessageId).toBeNull()
    expect(result.current.voiceNotice).toMatch(/无法播放/)
  })

  it('reports an interrupted stream and stops current audio', async () => {
    vi.mocked(api.streamSpeech).mockImplementation(async (_text, _signal, onEvent) => {
      onEvent({ event: 'started', requestId: 'request-1', engine: 'gpt-sovits', device: 'cuda' })
      onEvent({ event: 'fragment', requestId: 'request-1', index: 0, audio: new Blob(['one']) })
      throw new ApiError('TTS_STREAM_INTERRUPTED', '语音生成中断，文字回复不受影响。', true)
    })
    const { result } = renderHook(() => useSpeechPlayback())
    let outcome: string | undefined
    await act(async () => { outcome = await result.current.play(message) })
    expect(outcome).toBe('failed')
    expect(result.current.voiceNotice).toMatch(/语音生成中断/)
    expect(FakeAudio.instances[0].pause).toHaveBeenCalled()
  })

  it('clears the old audio queue when the backend requests recovery reset', async () => {
    vi.mocked(api.streamSpeech).mockImplementation(async (_text, _signal, onEvent) => {
      onEvent({ event: 'started', requestId: 'request-1', engine: 'gpt-sovits', device: 'cuda' })
      onEvent({ event: 'fragment', requestId: 'request-1', index: 0, audio: new Blob(['cuda']) })
      onEvent({ event: 'reset', requestId: 'request-1', engine: 'gpt-sovits', device: 'cpu', reason: 'primary_failed' })
      onEvent({ event: 'fragment', requestId: 'request-1', index: 0, audio: new Blob(['cpu']) })
      onEvent({ event: 'completed', requestId: 'request-1', fragmentCount: 1, generationSeconds: 2 })
    })
    const { result } = renderHook(() => useSpeechPlayback())
    let playback!: Promise<string>

    await act(async () => { playback = result.current.play(message); await Promise.resolve() })
    await waitFor(() => expect(FakeAudio.instances).toHaveLength(2))
    expect(FakeAudio.instances[0].pause).toHaveBeenCalled()

    act(() => FakeAudio.instances[1].onended?.())
    await expect(playback).resolves.toBe('ended')
    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(2)
  })
})
