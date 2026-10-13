import { useCallback, useEffect, useRef, useState } from 'react'

const MAX_SECONDS = 5 * 60

/** Microphone recording in the browser (MediaRecorder), with a live level and timer.
 * Needs a secure context: localhost, or a LAN address the browser treats as secure. */
export function useVoiceRecorder() {
  const [recording, setRecording] = useState(false)
  const [seconds, setSeconds] = useState(0)
  const [level, setLevel] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const rec = useRef<MediaRecorder | null>(null)
  const stream = useRef<MediaStream | null>(null)
  const chunks = useRef<Blob[]>([])
  const resolve = useRef<((b: Blob | null) => void) | null>(null)
  const timer = useRef<number | null>(null)
  const ctx = useRef<AudioContext | null>(null)

  const supported =
    typeof window !== 'undefined' &&
    window.isSecureContext &&
    !!navigator.mediaDevices?.getUserMedia &&
    typeof MediaRecorder !== 'undefined'

  const cleanup = useCallback(() => {
    if (timer.current) cancelAnimationFrame(timer.current)
    timer.current = null
    stream.current?.getTracks().forEach((t) => t.stop())
    stream.current = null
    ctx.current?.close().catch(() => {})
    ctx.current = null
    setRecording(false)
    setLevel(0)
  }, [])

  useEffect(() => cleanup, [cleanup])

  const start = useCallback(async () => {
    setError(null)
    if (!supported) {
      setError('Voice answers need a secure connection (this PC, or a trusted address); type instead.')
      return false
    }
    try {
      const s = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
      })
      stream.current = s
      const type = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4'].find((t) =>
        MediaRecorder.isTypeSupported(t),
      )
      const r = new MediaRecorder(s, type ? { mimeType: type } : undefined)
      chunks.current = []
      r.ondataavailable = (e) => e.data.size && chunks.current.push(e.data)
      r.onstop = () => {
        const blob = chunks.current.length ? new Blob(chunks.current, { type: r.mimeType }) : null
        cleanup()
        resolve.current?.(blob)
        resolve.current = null
      }
      rec.current = r
      r.start(1000)
      setRecording(true)
      setSeconds(0)

      // Level meter + timer (and a hard stop so a forgotten mic doesn't record forever).
      const ac = new AudioContext()
      ctx.current = ac
      const analyser = ac.createAnalyser()
      analyser.fftSize = 512
      ac.createMediaStreamSource(s).connect(analyser)
      const buf = new Uint8Array(analyser.fftSize)
      const t0 = performance.now()
      const tick = () => {
        analyser.getByteTimeDomainData(buf)
        let peak = 0
        for (const v of buf) peak = Math.max(peak, Math.abs(v - 128) / 128)
        setLevel(peak)
        const secs = (performance.now() - t0) / 1000
        setSeconds(secs)
        if (secs < MAX_SECONDS) timer.current = requestAnimationFrame(tick)
        else if (r.state === 'recording') r.stop()
      }
      timer.current = requestAnimationFrame(tick)
      return true
    } catch (e) {
      cleanup()
      setError(
        e instanceof DOMException && e.name === 'NotAllowedError'
          ? 'Microphone access was blocked. Allow it in the browser to answer by voice.'
          : 'Could not start the microphone.',
      )
      return false
    }
  }, [cleanup, supported])

  /** Stops and returns the recording (null if nothing was captured). */
  const stop = useCallback(
    () =>
      new Promise<Blob | null>((res) => {
        const r = rec.current
        if (!r || r.state !== 'recording') return res(null)
        resolve.current = res
        r.stop()
      }),
    [],
  )

  const cancel = useCallback(() => {
    resolve.current = null
    const r = rec.current
    if (r && r.state === 'recording') {
      r.onstop = () => cleanup()
      r.stop()
    } else cleanup()
  }, [cleanup])

  return { supported, recording, seconds, level, error, start, stop, cancel }
}
