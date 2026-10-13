import { Loader2, Mic, Send, Square, X } from 'lucide-react'
import { useState, type KeyboardEvent } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { useVoiceRecorder } from '@/hooks/useVoiceRecorder'
import { api } from '@/lib/api'
import { formatDuration } from '@/lib/dates'
import { cn } from '@/lib/utils'

/** Answer box: speak (recorded, transcribed by Whisper, editable before sending) or type. */
export function VoiceAnswer({
  subjectId,
  onSend,
  busy,
  placeholder = 'Type your answer, or press the microphone and speak',
}: {
  subjectId: number
  onSend: (text: string, spoken: boolean) => void
  busy?: boolean
  placeholder?: string
}) {
  const [text, setText] = useState('')
  const [spoken, setSpoken] = useState(false)
  const [transcribing, setTranscribing] = useState(false)
  const mic = useVoiceRecorder()

  const toggleMic = async () => {
    if (!mic.recording) return void (await mic.start())
    const blob = await mic.stop()
    if (!blob) return
    setTranscribing(true)
    try {
      const r = await api.transcribeVoice(blob, subjectId)
      if (!r.text.trim()) toast.warning('No speech recognised. Try again a bit closer to the mic.')
      else {
        setText((t) => (t.trim() ? `${t.trim()} ${r.text.trim()}` : r.text.trim()))
        setSpoken(true)
      }
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setTranscribing(false)
    }
  }

  const send = () => {
    if (!text.trim() || busy) return
    onSend(text.trim(), spoken)
    setText('')
    setSpoken(false)
  }
  const onKey = (e: KeyboardEvent) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault()
      send()
    }
  }

  return (
    <div className="space-y-2">
      <Textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKey}
        placeholder={transcribing ? 'Transcribing…' : placeholder}
        disabled={busy || transcribing}
        rows={4}
        aria-label="Your answer"
      />
      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant={mic.recording ? 'destructive' : 'outline'}
          onClick={toggleMic}
          disabled={busy || transcribing}
          aria-label={mic.recording ? 'Stop recording' : 'Answer by voice'}
        >
          {transcribing ? <Loader2 className="animate-spin" /> : mic.recording ? <Square /> : <Mic />}
          {mic.recording ? 'Stop' : transcribing ? 'Transcribing' : 'Speak'}
        </Button>
        {mic.recording && (
          <>
            <span className="font-mono text-sm tabular-nums">{formatDuration(mic.seconds)}</span>
            <span className="h-2 w-24 overflow-hidden rounded-full bg-muted" aria-hidden>
              <span
                className="block h-full bg-red-500 transition-[width] duration-75"
                style={{ width: `${Math.min(100, mic.level * 140)}%` }}
              />
            </span>
            <Button type="button" size="sm" variant="ghost" onClick={mic.cancel}>
              <X /> Discard
            </Button>
          </>
        )}
        {mic.error && <span className="text-xs text-destructive">{mic.error}</span>}
        <Button
          type="button"
          className={cn('ml-auto')}
          onClick={send}
          disabled={!text.trim() || busy || mic.recording || transcribing}
        >
          {busy ? <Loader2 className="animate-spin" /> : <Send />} Send
        </Button>
      </div>
      {spoken && !mic.recording && (
        <p className="text-xs text-muted-foreground">
          Check the transcript and fix misheard words before sending.
        </p>
      )}
    </div>
  )
}
