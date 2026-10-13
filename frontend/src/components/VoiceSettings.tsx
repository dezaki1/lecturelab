import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AudioLines, CheckCircle2, Download, Loader2, Play } from 'lucide-react'
import { useRef } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { useEvent } from '@/hooks/useEvents'
import { api } from '@/lib/api'
import type { VoiceStatus } from '@/lib/types'
import { cn } from '@/lib/utils'

/** Settings card: Piper voices (download, pick, test), speaking speed, speech recognition. */
export function VoiceSettings() {
  const qc = useQueryClient()
  const { data: v } = useQuery({ queryKey: ['voice-settings'], queryFn: api.voiceSettings })
  const audio = useRef<HTMLAudioElement | null>(null)
  useEvent('voice', (d: { state: string; error?: string; voice: string }) => {
    qc.invalidateQueries({ queryKey: ['voice-settings'] })
    if (d.state === 'done') toast.success('Voice downloaded')
    if (d.state === 'failed') toast.error(`Voice download failed: ${d.error}`)
  })
  const set = (d: VoiceStatus) => qc.setQueryData(['voice-settings'], d)
  const onError = (e: Error) => toast.error(e.message)
  const save = useMutation({ mutationFn: api.putVoiceSettings, onSuccess: set, onError })
  const download = useMutation({
    mutationFn: api.downloadVoice,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['voice-settings'] }),
    onError,
  })
  if (!v) return null
  const test = (voice: string) => {
    audio.current?.pause()
    audio.current = new Audio(api.voiceSampleUrl(voice))
    audio.current.play().catch(() => toast.error('Could not play the sample'))
  }

  return (
    <section className="mt-6 space-y-4 rounded-lg border bg-card p-5 text-sm">
      <div>
        <h2 className="flex items-center gap-2 font-semibold">
          <AudioLines className="size-4" /> Voice
        </h2>
        <p className="mt-1 text-muted-foreground">
          The examiner&apos;s voice in oral practice, generated on this PC with Piper (no cloud). Your spoken
          answers are transcribed locally by Whisper ({v.stt.model}
          {v.stt.loaded ? ', loaded now' : ', loads on the first answer and unloads after 5 idle minutes'}).
        </p>
      </div>
      <ul className="divide-y rounded-md border">
        {v.voices.map((x) => {
          const dl = x.download?.state === 'running' ? x.download : null
          const selected = v.voice === x.id
          return (
            <li key={x.id} className="flex items-center gap-3 px-3 py-2">
              <button
                type="button"
                disabled={!x.installed}
                onClick={() => save.mutate({ voice: x.id })}
                className={cn(
                  'flex min-w-0 flex-1 items-center gap-2 text-left',
                  !x.installed && 'text-muted-foreground',
                )}
                aria-pressed={selected}
              >
                <span
                  className={cn(
                    'size-3.5 shrink-0 rounded-full border',
                    selected && x.installed && 'border-4 border-primary',
                  )}
                />
                <span className="truncate">{x.label}</span>
              </button>
              {x.installed ? (
                <>
                  {selected && <CheckCircle2 className="size-4 text-emerald-500" aria-label="In use" />}
                  <Button
                    size="icon-xs"
                    variant="ghost"
                    aria-label={`Test ${x.label}`}
                    onClick={() => test(x.id)}
                  >
                    <Play />
                  </Button>
                </>
              ) : dl ? (
                <span className="flex items-center gap-2 text-xs text-muted-foreground tabular-nums">
                  <Loader2 className="size-3.5 animate-spin" /> {Math.round(dl.fraction * 100)}%
                </span>
              ) : (
                <Button size="xs" variant="outline" onClick={() => download.mutate(x.id)}>
                  <Download /> {x.size_mb} MB
                </Button>
              )}
            </li>
          )
        })}
      </ul>
      <div className="flex items-center gap-3">
        <Label htmlFor="voice-rate">Speed</Label>
        <input
          id="voice-rate"
          type="range"
          min={0.8}
          max={1.3}
          step={0.05}
          defaultValue={v.rate}
          onChange={(e) => save.mutate({ rate: Number(e.target.value) })}
          className="w-40 accent-primary"
        />
        <span className="w-10 text-xs text-muted-foreground tabular-nums">{v.rate.toFixed(2)}×</span>
      </div>
    </section>
  )
}
