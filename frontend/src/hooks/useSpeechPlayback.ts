import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, api } from '../api/client'
import type { Message } from '../types'

export type SpeechPlaybackResult = 'ended' | 'blocked' | 'failed' | 'stopped'

// A silent, valid WAV used only to establish media playback from a user gesture.
// Without this primer, the later response arrives after fetch/network work and
// Chromium treats audio.play() as unsolicited autoplay.
const SILENT_WAV_DATA_URL = 'data:audio/wav;base64,UklGRiYAAABXQVZFZm10IBAAAAABAAEAQB8AAIA+AAACABAAZGF0YQIAAAAAAA=='

export function useSpeechPlayback() {
  const [autoRead, setAutoReadState] = useState(true)
  const [activeMessageId, setActiveMessageId] = useState<string | null>(null)
  const [loadingMessageId, setLoadingMessageId] = useState<string | null>(null)
  const [voiceNotice, setVoiceNotice] = useState<string | null>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const activeIdRef = useRef<string | null>(null)
  const waiterRef = useRef<((result: SpeechPlaybackResult) => void) | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const queueRef = useRef<string[]>([])
  const objectUrlsRef = useRef(new Set<string>())
  const streamCompletedRef = useRef(false)
  const playSequenceRef = useRef(0)
  const playbackUnlockedRef = useRef(false)

  const prepareForPlayback = useCallback(() => {
    if (playbackUnlockedRef.current || typeof Audio === 'undefined') return
    const primer = new Audio(SILENT_WAV_DATA_URL)
    primer.muted = true
    primer.volume = 0
    void primer.play().then(() => {
      primer.pause()
      primer.removeAttribute('src')
      primer.load()
      playbackUnlockedRef.current = true
    }).catch(() => {
      // The real audio play call will surface a clear, actionable notice.
    })
  }, [])

  const updateActive = useCallback((messageId: string | null) => {
    activeIdRef.current = messageId
    setActiveMessageId(messageId)
  }, [])

  const settle = useCallback((result: SpeechPlaybackResult) => {
    const waiter = waiterRef.current
    waiterRef.current = null
    waiter?.(result)
  }, [])

  const revokeUrl = useCallback((url: string) => {
    if (!objectUrlsRef.current.delete(url)) return
    URL.revokeObjectURL(url)
  }, [])

  const clearQueuedUrls = useCallback(() => {
    for (const url of queueRef.current) revokeUrl(url)
    queueRef.current = []
  }, [revokeUrl])

  const stopCurrent = useCallback((result: SpeechPlaybackResult = 'stopped') => {
    abortRef.current?.abort()
    abortRef.current = null
    const audio = audioRef.current
    if (audio) {
      const source = audio.src
      audio.onended = null
      audio.onerror = null
      audio.pause()
      audio.currentTime = 0
      audioRef.current = null
      revokeUrl(source)
    }
    clearQueuedUrls()
    streamCompletedRef.current = false
    setLoadingMessageId(null)
    updateActive(null)
    settle(result)
  }, [clearQueuedUrls, revokeUrl, settle, updateActive])

  const stop = useCallback(() => {
    playSequenceRef.current += 1
    setLoadingMessageId(null)
    stopCurrent('stopped')
  }, [stopCurrent])

  const play = useCallback(async (message: Message): Promise<SpeechPlaybackResult> => {
    if (activeIdRef.current === message.id && audioRef.current) {
      stop()
      return 'stopped'
    }

    const sequence = playSequenceRef.current + 1
    playSequenceRef.current = sequence
    stopCurrent('stopped')
    setVoiceNotice(null)
    setLoadingMessageId(message.id)
    streamCompletedRef.current = false
    const controller = new AbortController()
    abortRef.current = controller

    return await new Promise<SpeechPlaybackResult>((resolve) => {
      waiterRef.current = resolve

      const fail = (notice: string, result: SpeechPlaybackResult = 'failed') => {
        if (sequence !== playSequenceRef.current) return
        stopCurrent(result)
        setVoiceNotice(notice)
      }

      const playNext = () => {
        if (sequence !== playSequenceRef.current || audioRef.current) return
        const objectUrl = queueRef.current.shift()
        if (!objectUrl) {
          updateActive(null)
          if (streamCompletedRef.current) {
            setLoadingMessageId(null)
            abortRef.current = null
            settle('ended')
          } else {
            setLoadingMessageId(message.id)
          }
          return
        }
        setLoadingMessageId(null)
        const audio = new Audio(objectUrl)
        audioRef.current = audio
        updateActive(message.id)
        audio.onended = () => {
          if (audioRef.current !== audio) return
          audioRef.current = null
          revokeUrl(objectUrl)
          playNext()
        }
        audio.onerror = () => fail('音频无法播放，文字回复仍然可用。')
        void audio.play().catch((playError: unknown) => {
          if (audioRef.current !== audio) return
          if (playError instanceof DOMException && playError.name === 'NotAllowedError') {
            fail('浏览器阻止了自动播放，请点击回复旁的“播放”。', 'blocked')
          } else {
            fail('本地语音暂时不可用，文字回复不受影响。')
          }
        })
      }

      const resetPlayback = () => {
        const audio = audioRef.current
        if (audio) {
          const source = audio.src
          audio.onended = null
          audio.onerror = null
          audio.pause()
          audio.currentTime = 0
          audioRef.current = null
          revokeUrl(source)
        }
        clearQueuedUrls()
        streamCompletedRef.current = false
        setLoadingMessageId(message.id)
        updateActive(null)
      }

      void api.streamSpeech(message.content, controller.signal, (event) => {
        if (sequence !== playSequenceRef.current) return
        if (event.event === 'reset') {
          resetPlayback()
        } else if (event.event === 'fragment') {
          const objectUrl = URL.createObjectURL(event.audio)
          objectUrlsRef.current.add(objectUrl)
          queueRef.current.push(objectUrl)
          playNext()
        } else if (event.event === 'completed') {
          streamCompletedRef.current = true
          playNext()
        }
      }).catch((requestError: unknown) => {
        if (sequence !== playSequenceRef.current || controller.signal.aborted) return
        if (requestError instanceof ApiError) fail(requestError.message)
        else fail('本地语音暂时不可用，文字回复不受影响。')
      })
    })
  }, [revokeUrl, settle, stop, stopCurrent, updateActive])

  const setAutoRead = useCallback((enabled: boolean) => {
    setAutoReadState(enabled)
    if (!enabled) stop()
  }, [stop])

  useEffect(() => () => {
    stopCurrent('stopped')
    for (const objectUrl of objectUrlsRef.current.values()) URL.revokeObjectURL(objectUrl)
    objectUrlsRef.current.clear()
  }, [stopCurrent])

  return { autoRead, activeMessageId, loadingMessageId, voiceNotice, play, stop, setAutoRead, prepareForPlayback }
}
