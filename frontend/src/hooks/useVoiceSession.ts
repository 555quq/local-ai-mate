import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, api } from '../api/client'
import type { VoicePhase } from '../types'
import { createVoiceActivityTracker } from '../voice/activity'

const DEFAULT_MAX_TURN_MS = 15_000
const DEFAULT_SILENCE_MS = 900
// Laptop microphones commonly report ordinary speech around 0.01–0.03 RMS.
// Keep this above a quiet-room noise floor without requiring the user to shout.
const SPEECH_THRESHOLD = 0.012
const RECORDER_TYPES = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/webm', 'audio/ogg']

interface UseVoiceSessionOptions {
  suspended: boolean
  onTranscript: (text: string) => void | Promise<void>
  maxTurnMs?: number
  silenceMs?: number
}

interface VoiceSession {
  active: boolean
  phase: VoicePhase
  error: string | null
  start: () => Promise<void>
  stop: () => void
  setPhase: (phase: VoicePhase) => void
}

interface RecordingRound {
  recorder: MediaRecorder
  chunks: Blob[]
  decision: 'submit' | 'discard'
  settled: boolean
}

function recorderMimeType(): string | undefined {
  if (typeof MediaRecorder.isTypeSupported !== 'function') return undefined
  return RECORDER_TYPES.find((type) => MediaRecorder.isTypeSupported(type))
}

function microphoneError(error: unknown): string {
  if (error instanceof DOMException && (error.name === 'NotAllowedError' || error.name === 'SecurityError')) {
    return '无法使用麦克风，请在浏览器设置中允许访问后重试。'
  }
  if (error instanceof DOMException && error.name === 'NotFoundError') return '没有找到可用的麦克风，文字输入仍可使用。'
  return '麦克风暂时不可用，文字输入仍可使用。'
}

export function useVoiceSession({
  suspended,
  onTranscript,
  maxTurnMs = DEFAULT_MAX_TURN_MS,
  silenceMs = DEFAULT_SILENCE_MS,
}: UseVoiceSessionOptions): VoiceSession {
  const [active, setActive] = useState(false)
  const [phaseState, setPhaseState] = useState<VoicePhase>('idle')
  const [error, setError] = useState<string | null>(null)
  const activeRef = useRef(false)
  const startingRef = useRef(false)
  const mountedRef = useRef(true)
  const generationRef = useRef(0)
  const suspendedRef = useRef(suspended)
  const previousSuspendedRef = useRef(suspended)
  const onTranscriptRef = useRef(onTranscript)
  const streamRef = useRef<MediaStream | null>(null)
  const trackEndedListenersRef = useRef<Array<{ track: MediaStreamTrack; listener: EventListener }>>([])
  const audioContextRef = useRef<AudioContext | null>(null)
  const analyserRef = useRef<AnalyserNode | null>(null)
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null)
  const roundRef = useRef<RecordingRound | null>(null)
  const frameRef = useRef<number | null>(null)
  const startRoundRef = useRef<() => Promise<void>>(async () => undefined)

  suspendedRef.current = suspended
  onTranscriptRef.current = onTranscript

  const setPhase = useCallback((nextPhase: VoicePhase) => {
    if (mountedRef.current) setPhaseState(nextPhase)
  }, [])

  const cancelFrame = useCallback(() => {
    if (frameRef.current !== null) {
      cancelAnimationFrame(frameRef.current)
      frameRef.current = null
    }
  }, [])

  const releaseResources = useCallback((nextPhase: VoicePhase, updateState = true) => {
    generationRef.current += 1
    activeRef.current = false
    startingRef.current = false
    cancelFrame()

    const round = roundRef.current
    roundRef.current = null
    if (round) {
      round.settled = true
      round.recorder.ondataavailable = null
      round.recorder.onstop = null
      if (round.recorder.state !== 'inactive') round.recorder.stop()
    }

    sourceRef.current?.disconnect()
    analyserRef.current?.disconnect()
    sourceRef.current = null
    analyserRef.current = null

    const context = audioContextRef.current
    audioContextRef.current = null
    if (context && context.state !== 'closed') void context.close()

    const stream = streamRef.current
    streamRef.current = null
    for (const { track, listener } of trackEndedListenersRef.current) track.removeEventListener('ended', listener)
    trackEndedListenersRef.current = []
    stream?.getTracks().forEach((track) => track.stop())

    if (updateState && mountedRef.current) {
      setActive(false)
      setPhaseState(nextPhase)
    }
  }, [cancelFrame])

  const failSession = useCallback((message: string) => {
    setError(message)
    releaseResources('error')
  }, [releaseResources])

  const finishRound = useCallback((round: RecordingRound, decision: 'submit' | 'discard') => {
    if (round.settled) return
    round.decision = decision
    cancelFrame()
    if (round.recorder.state !== 'inactive') round.recorder.stop()
  }, [cancelFrame])

  const startRound = useCallback(async () => {
    const stream = streamRef.current
    const context = audioContextRef.current
    const analyser = analyserRef.current
    if (!activeRef.current || suspendedRef.current || !stream || !context || !analyser || roundRef.current) return

    try {
      stream.getTracks().forEach((track) => { track.enabled = true })
      if (context.state === 'suspended') await context.resume()
      if (!activeRef.current || suspendedRef.current || roundRef.current) return

      const mimeType = recorderMimeType()
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
      const round: RecordingRound = { recorder, chunks: [], decision: 'discard', settled: false }
      const tracker = createVoiceActivityTracker({ threshold: SPEECH_THRESHOLD, silenceMs, maxTurnMs })
      const samples = new Float32Array(analyser.fftSize)
      const startedAt = performance.now()
      roundRef.current = round

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) round.chunks.push(event.data)
      }
      recorder.onstop = () => {
        if (round.settled) return
        round.settled = true
        if (roundRef.current === round) roundRef.current = null
        recorder.ondataavailable = null
        recorder.onstop = null

        const complete = async () => {
          if (!activeRef.current) return
          if (round.decision === 'discard') {
            if (!suspendedRef.current) await startRoundRef.current()
            return
          }

          stream.getTracks().forEach((track) => { track.enabled = false })
          if (context.state === 'running') void context.suspend()
          setPhase('transcribing')
          const audio = new Blob(round.chunks, { type: recorder.mimeType || mimeType || 'audio/webm' })
          if (audio.size === 0) {
            if (!suspendedRef.current) await startRoundRef.current()
            return
          }

          try {
            const transcript = await api.transcribeSpeech(audio)
            if (!activeRef.current) return
            const text = transcript.text.trim()
            if (!text) {
              if (!suspendedRef.current) await startRoundRef.current()
              return
            }
            setPhase('thinking')
            await onTranscriptRef.current(text)
          } catch (requestError) {
            if (!activeRef.current) return
            failSession(requestError instanceof ApiError ? requestError.message : '本地语音识别暂时不可用，文字输入仍可使用。')
          }
        }
        void complete()
      }

      // Periodic chunks make browser recorder shutdown reliable after a short
      // utterance; everything still remains in memory until this turn ends.
      recorder.start(250)
      setPhase('listening')
      const inspect = (now: number) => {
        if (!activeRef.current || suspendedRef.current || round.settled || roundRef.current !== round) return
        analyser.getFloatTimeDomainData(samples)
        let squareSum = 0
        for (const sample of samples) squareSum += sample * sample
        const level = Math.sqrt(squareSum / samples.length)
        if (level >= SPEECH_THRESHOLD) setPhase('recording')
        const decision = tracker.update(level, now - startedAt)
        if (decision === 'continue') frameRef.current = requestAnimationFrame(inspect)
        else finishRound(round, decision)
      }
      frameRef.current = requestAnimationFrame(inspect)
    } catch (roundError) {
      failSession(microphoneError(roundError))
    }
  }, [failSession, finishRound, maxTurnMs, setPhase, silenceMs])

  startRoundRef.current = startRound

  const start = useCallback(async () => {
    if (activeRef.current || startingRef.current) return
    startingRef.current = true
    const generation = generationRef.current + 1
    generationRef.current = generation
    setError(null)
    setPhaseState('requesting')

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      if (!mountedRef.current || generationRef.current !== generation) {
        stream.getTracks().forEach((track) => track.stop())
        return
      }

      streamRef.current = stream
      const context = new AudioContext()
      audioContextRef.current = context
      const analyser = context.createAnalyser()
      analyser.fftSize = 2048
      const source = context.createMediaStreamSource(stream)
      source.connect(analyser)
      analyserRef.current = analyser
      sourceRef.current = source
      activeRef.current = true
      startingRef.current = false
      trackEndedListenersRef.current = stream.getTracks().map((track) => {
        const listener = () => {
          if (activeRef.current) failSession('麦克风访问已中断，文字输入仍可使用。')
        }
        track.addEventListener('ended', listener, { once: true })
        return { track, listener }
      })
      setActive(true)

      if (suspendedRef.current) {
        stream.getTracks().forEach((track) => { track.enabled = false })
        if (context.state === 'running') void context.suspend()
        setPhase('listening')
      } else {
        await startRoundRef.current()
      }
    } catch (permissionError) {
      startingRef.current = false
      if (generationRef.current === generation) failSession(microphoneError(permissionError))
    }
  }, [failSession, setPhase])

  const stop = useCallback(() => {
    setError(null)
    releaseResources('idle')
  }, [releaseResources])

  useEffect(() => {
    const wasSuspended = previousSuspendedRef.current
    previousSuspendedRef.current = suspended
    if (!activeRef.current) return

    const stream = streamRef.current
    const context = audioContextRef.current
    if (suspended) {
      stream?.getTracks().forEach((track) => { track.enabled = false })
      const round = roundRef.current
      if (round) finishRound(round, 'discard')
      if (context?.state === 'running') void context.suspend()
    } else if (wasSuspended) {
      stream?.getTracks().forEach((track) => { track.enabled = true })
      void startRoundRef.current()
    }
  }, [finishRound, suspended])

  useEffect(() => {
    // React StrictMode intentionally runs setup -> cleanup -> setup in
    // development. Resetting this flag in setup prevents an allowed microphone
    // stream from being mistaken for a late result from an unmounted component.
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      releaseResources('idle', false)
    }
  }, [releaseResources])

  return { active, phase: phaseState, error, start, stop, setPhase }
}
