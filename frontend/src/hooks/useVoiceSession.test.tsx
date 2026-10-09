import { StrictMode, type PropsWithChildren } from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from '../api/client'
import { useVoiceSession } from './useVoiceSession'

vi.mock('../api/client', async () => {
  const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
  return { ...actual, api: { ...actual.api, transcribeSpeech: vi.fn() } }
})

class FakeTrack {
  enabled = true
  stop = vi.fn()
  private endedListeners = new Set<EventListenerOrEventListenerObject>()
  addEventListener = vi.fn((type: string, listener: EventListenerOrEventListenerObject) => {
    if (type === 'ended') this.endedListeners.add(listener)
  })
  removeEventListener = vi.fn((type: string, listener: EventListenerOrEventListenerObject) => {
    if (type === 'ended') this.endedListeners.delete(listener)
  })
  end() {
    const event = new Event('ended')
    for (const listener of this.endedListeners) {
      if (typeof listener === 'function') listener(event)
      else listener.handleEvent(event)
    }
  }
}

class FakeMediaRecorder {
  static instances: FakeMediaRecorder[] = []
  static isTypeSupported = vi.fn(() => true)
  readonly stream: MediaStream
  readonly mimeType: string
  state: RecordingState = 'inactive'
  ondataavailable: ((event: BlobEvent) => void) | null = null
  onstop: (() => void) | null = null

  constructor(stream: MediaStream, options?: MediaRecorderOptions) {
    this.stream = stream
    this.mimeType = options?.mimeType ?? 'audio/webm'
    FakeMediaRecorder.instances.push(this)
  }

  start = vi.fn((_timeslice?: number) => { this.state = 'recording' })
  stop = vi.fn(() => {
    if (this.state === 'inactive') return
    this.state = 'inactive'
    queueMicrotask(() => this.onstop?.())
  })

  emit(blob: Blob) {
    this.ondataavailable?.({ data: blob } as BlobEvent)
  }
}

class FakeAnalyser {
  fftSize = 0
  sample = 0
  getFloatTimeDomainData(buffer: Float32Array) { buffer.fill(this.sample) }
  disconnect = vi.fn()
}

class FakeSource {
  connect = vi.fn()
  disconnect = vi.fn()
}

class FakeAudioContext {
  static instances: FakeAudioContext[] = []
  readonly analyser = new FakeAnalyser()
  readonly source = new FakeSource()
  state: AudioContextState = 'running'
  createAnalyser = vi.fn(() => this.analyser)
  createMediaStreamSource = vi.fn(() => this.source)
  suspend = vi.fn(async () => { this.state = 'suspended' })
  resume = vi.fn(async () => { this.state = 'running' })
  close = vi.fn(async () => { this.state = 'closed' })

  constructor() { FakeAudioContext.instances.push(this) }
}

let rafCallbacks = new Map<number, FrameRequestCallback>()
let nextRafId = 1
let tracks: FakeTrack[]

function runFrame(time: number) {
  const entries = [...rafCallbacks.entries()]
  rafCallbacks.clear()
  for (const [, callback] of entries) callback(time)
}

function latestRecorder() {
  const recorder = FakeMediaRecorder.instances.at(-1)
  if (!recorder) throw new Error('No MediaRecorder was created')
  return recorder
}

describe('useVoiceSession', () => {
  beforeEach(() => {
    tracks = [new FakeTrack(), new FakeTrack()]
    FakeMediaRecorder.instances = []
    FakeAudioContext.instances = []
    rafCallbacks = new Map()
    nextRafId = 1
    vi.stubGlobal('MediaRecorder', FakeMediaRecorder)
    vi.stubGlobal('AudioContext', FakeAudioContext)
    vi.stubGlobal('requestAnimationFrame', vi.fn((callback: FrameRequestCallback) => {
      const id = nextRafId++
      rafCallbacks.set(id, callback)
      return id
    }))
    vi.stubGlobal('cancelAnimationFrame', vi.fn((id: number) => { rafCallbacks.delete(id) }))
    vi.spyOn(performance, 'now').mockReturnValue(0)
    Object.defineProperty(navigator, 'mediaDevices', {
      configurable: true,
      value: { getUserMedia: vi.fn(async () => ({ getTracks: () => tracks } as unknown as MediaStream)) },
    })
    vi.mocked(api.transcribeSpeech).mockReset()
  })

  afterEach(() => vi.unstubAllGlobals())

  it('reports permission errors and returns to an inactive, recoverable state', async () => {
    vi.mocked(navigator.mediaDevices.getUserMedia).mockRejectedValueOnce(new DOMException('denied', 'NotAllowedError'))
    const { result } = renderHook(() => useVoiceSession({ suspended: false, onTranscript: vi.fn() }))

    await act(async () => { await result.current.start() })

    expect(result.current.active).toBe(false)
    expect(result.current.phase).toBe('error')
    expect(result.current.error).toMatch(/麦克风/)
  })

  it('remains mounted and keeps the microphone active under React StrictMode', async () => {
    const wrapper = ({ children }: PropsWithChildren) => <StrictMode>{children}</StrictMode>
    const { result } = renderHook(
      () => useVoiceSession({ suspended: false, onTranscript: vi.fn() }),
      { wrapper },
    )

    await act(async () => { await result.current.start() })

    expect(result.current.active).toBe(true)
    expect(result.current.phase).toBe('listening')
    expect(tracks.every((track) => track.stop.mock.calls.length === 0)).toBe(true)
    expect(latestRecorder().start).toHaveBeenCalledWith(250)
  })

  it('stops every track and closes browser audio resources exactly once', async () => {
    const { result } = renderHook(() => useVoiceSession({ suspended: false, onTranscript: vi.fn() }))
    await act(async () => { await result.current.start() })

    act(() => { result.current.stop(); result.current.stop() })
    await waitFor(() => expect(FakeAudioContext.instances[0].close).toHaveBeenCalledTimes(1))
    expect(tracks.every((track) => track.stop.mock.calls.length === 1)).toBe(true)
    expect(cancelAnimationFrame).toHaveBeenCalled()
    expect(result.current.active).toBe(false)
    expect(result.current.phase).toBe('idle')
  })

  it('ends the session and releases all tracks when microphone access is revoked', async () => {
    const { result } = renderHook(() => useVoiceSession({ suspended: false, onTranscript: vi.fn() }))
    await act(async () => { await result.current.start() })

    act(() => tracks[0].end())

    await waitFor(() => expect(result.current.phase).toBe('error'))
    expect(result.current.active).toBe(false)
    expect(result.current.error).toMatch(/麦克风/)
    expect(tracks.every((track) => track.stop.mock.calls.length === 1)).toBe(true)
  })

  it('discards a silent turn without calling STT', async () => {
    const { result } = renderHook(() => useVoiceSession({ suspended: false, onTranscript: vi.fn(), maxTurnMs: 15_000 }))
    await act(async () => { await result.current.start() })
    latestRecorder().emit(new Blob(['silence'], { type: 'audio/webm' }))

    act(() => runFrame(15_000))
    await waitFor(() => expect(FakeMediaRecorder.instances.length).toBe(2))
    expect(api.transcribeSpeech).not.toHaveBeenCalled()
    expect(result.current.phase).toBe('listening')
  })

  it('submits a voiced Blob and passes a non-empty transcript to the callback', async () => {
    vi.mocked(api.transcribeSpeech).mockResolvedValue({ text: ' 你好，露娜 ', language: 'zh', duration_ms: 800 })
    const onTranscript = vi.fn()
    const { result } = renderHook(() => useVoiceSession({ suspended: false, onTranscript, silenceMs: 900 }))
    await act(async () => { await result.current.start() })
    const recorder = latestRecorder()
    recorder.emit(new Blob(['voice'], { type: 'audio/webm' }))

    FakeAudioContext.instances[0].analyser.sample = 0.2
    act(() => runFrame(100))
    FakeAudioContext.instances[0].analyser.sample = 0
    act(() => runFrame(1_000))

    await waitFor(() => expect(onTranscript).toHaveBeenCalledWith('你好，露娜'))
    expect(api.transcribeSpeech).toHaveBeenCalledTimes(1)
    const sentBlob = vi.mocked(api.transcribeSpeech).mock.calls[0][0]
    expect(sentBlob).toBeInstanceOf(Blob)
    expect(sentBlob.type).toBe('audio/webm;codecs=opus')
  })

  it('pauses collection while suspended and resumes without another permission request', async () => {
    const onTranscript = vi.fn()
    const { result, rerender } = renderHook(
      ({ suspended }) => useVoiceSession({ suspended, onTranscript }),
      { initialProps: { suspended: false } },
    )
    await act(async () => { await result.current.start() })
    const firstRecorder = latestRecorder()

    rerender({ suspended: true })
    await waitFor(() => expect(firstRecorder.stop).toHaveBeenCalled())
    expect(tracks.every((track) => !track.enabled)).toBe(true)
    expect(FakeAudioContext.instances[0].suspend).toHaveBeenCalled()

    rerender({ suspended: false })
    await waitFor(() => expect(FakeMediaRecorder.instances.length).toBe(2))
    expect(tracks.every((track) => track.enabled)).toBe(true)
    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledTimes(1)
  })

  it('cleans up MediaRecorder, AudioContext, RAF and tracks on unmount', async () => {
    const { result, unmount } = renderHook(() => useVoiceSession({ suspended: false, onTranscript: vi.fn() }))
    await act(async () => { await result.current.start() })
    const recorder = latestRecorder()

    unmount()

    expect(recorder.stop).toHaveBeenCalled()
    expect(cancelAnimationFrame).toHaveBeenCalled()
    expect(tracks.every((track) => track.stop.mock.calls.length === 1)).toBe(true)
    await waitFor(() => expect(FakeAudioContext.instances[0].close).toHaveBeenCalledTimes(1))
  })
})

describe('api.transcribeSpeech', () => {
  it('sends the original Blob body with its browser media type', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => ({
      ok: true,
      json: async () => ({
        request_id: 'request-1',
        server_time: '2026-10-08T00:00:00Z',
        data: { text: '你好', language: 'zh', duration_ms: 320 },
      }),
    }))
    vi.stubGlobal('fetch', fetchMock)
    const actual = await vi.importActual<typeof import('../api/client')>('../api/client')
    const blob = new Blob(['voice'], { type: 'audio/ogg;codecs=opus' })

    await expect(actual.api.transcribeSpeech(blob)).resolves.toEqual({ text: '你好', language: 'zh', duration_ms: 320 })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, options] = fetchMock.mock.calls[0]
    expect(options).toBeDefined()
    expect(url).toBe('/api/v1/stt/transcribe')
    expect(options?.body).toBe(blob)
    expect(options?.headers).toMatchObject({ 'Content-Type': 'audio/ogg;codecs=opus' })
    vi.unstubAllGlobals()
  })
})
