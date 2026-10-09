import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api } from './client'

const encoder = new TextEncoder()

function streamingResponse(chunks: string[], status = 200): Response {
  return new Response(new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  }), { status, headers: { 'Content-Type': 'application/x-ndjson' } })
}

function lines(audioBase64 = 'UklGRgAAAABXQVZF'): string[] {
  return [
    JSON.stringify({ event: 'started', request_id: 'r1', engine: 'gpt-sovits', device: 'cuda' }),
    JSON.stringify({ event: 'fragment', request_id: 'r1', index: 0, media_type: 'audio/wav', audio_base64: audioBase64 }),
    JSON.stringify({ event: 'completed', request_id: 'r1', fragment_count: 1, generation_seconds: 1.2 }),
  ]
}

describe('streamSpeech', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('parses split and combined NDJSON chunks in order', async () => {
    const payload = `${lines().join('\n')}\n`
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(streamingResponse([
      payload.slice(0, 17),
      payload.slice(17, 65),
      payload.slice(65),
    ])))
    const events: string[] = []
    await api.streamSpeech('你好', new AbortController().signal, (event) => events.push(event.event))
    expect(events).toEqual(['started', 'fragment', 'completed'])
  })

  it('rejects invalid base64 audio', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(streamingResponse([`${lines('%%%').join('\n')}\n`])))
    await expect(api.streamSpeech('你好', new AbortController().signal, () => undefined)).rejects.toMatchObject({
      code: 'TTS_STREAM_INVALID',
    })
  })

  it('rejects a stream that ends without completed', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(streamingResponse([`${lines().slice(0, 2).join('\n')}\n`])))
    await expect(api.streamSpeech('你好', new AbortController().signal, () => undefined)).rejects.toMatchObject({
      code: 'TTS_STREAM_INTERRUPTED',
    })
  })

  it('parses a recovery reset and restarts fragment ordering', async () => {
    const payload = [
      JSON.stringify({ event: 'started', request_id: 'r1', engine: 'gpt-sovits', device: 'cuda' }),
      JSON.stringify({ event: 'fragment', request_id: 'r1', index: 0, media_type: 'audio/wav', audio_base64: 'UklGRgAAAABXQVZF' }),
      JSON.stringify({ event: 'reset', request_id: 'r1', engine: 'gpt-sovits', device: 'cpu', reason: 'primary_failed' }),
      JSON.stringify({ event: 'fragment', request_id: 'r1', index: 0, media_type: 'audio/wav', audio_base64: 'UklGRgAAAABXQVZF' }),
      JSON.stringify({ event: 'completed', request_id: 'r1', fragment_count: 1, generation_seconds: 1.2 }),
    ].join('\n') + '\n'
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(streamingResponse([payload])))
    const events: string[] = []
    await api.streamSpeech('你好', new AbortController().signal, (event) => events.push(event.event))
    expect(events).toEqual(['started', 'fragment', 'reset', 'fragment', 'completed'])
  })

  it('maps an HTTP problem before streaming starts', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ error: { code: 'TTS_BUSY', message: '忙碌', retryable: true } }),
      { status: 429, headers: { 'Content-Type': 'application/json' } },
    )))
    await expect(api.streamSpeech('你好', new AbortController().signal, () => undefined)).rejects.toEqual(
      new ApiError('TTS_BUSY', '忙碌', true),
    )
  })
})
