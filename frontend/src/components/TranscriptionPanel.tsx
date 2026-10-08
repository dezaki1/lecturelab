import { useMutation, useQueryClient } from '@tanstack/react-query'
import { AudioLines, Cpu, Loader2, RotateCcw, TriangleAlert, Zap } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { api } from '@/lib/api'
import { formatDuration } from '@/lib/dates'
import type { LectureDetail } from '@/lib/types'

const LANGS: Record<string, string> = { en: 'English', de: 'German' }

/** Transcription status line: progress while running, retry on failure, stats when done. */
export function TranscriptionPanel({ lecture }: { lecture: LectureDetail }) {
  const qc = useQueryClient()
  const stage = lecture.stages.transcribe
  const rerun = useMutation({
    mutationFn: (language?: string) => api.retranscribe(lecture.id, language),
    onSuccess: (l) => qc.setQueryData(['lecture', lecture.id], l),
    onError: (e: Error) => toast.error(e.message),
  })
  if (!lecture.has_audio) return null

  const menu = (label: string, icon = <RotateCcw />) => (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button size="xs" variant="ghost" disabled={rerun.isPending}>
          {icon} {label}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuLabel>Spoken language</DropdownMenuLabel>
        <DropdownMenuItem onClick={() => rerun.mutate(undefined)}>Subject default</DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => rerun.mutate('en')}>English</DropdownMenuItem>
        <DropdownMenuItem onClick={() => rerun.mutate('de')}>German</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )

  if (stage === 'pending' || stage === 'running') {
    const pct = lecture.job_progress != null ? Math.round(lecture.job_progress * 100) : null
    return (
      <div className="mt-3 flex items-center gap-3 rounded-lg border bg-card px-4 py-2.5 text-sm">
        <Loader2 className="size-4 animate-spin text-primary" />
        <span>{stage === 'pending' ? 'Waiting to transcribe…' : lecture.job_message || 'Transcribing…'}</span>
        {pct != null && pct > 0 && (
          <>
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
              <div className="h-full bg-primary transition-[width]" style={{ width: `${pct}%` }} />
            </div>
            <span className="w-10 text-right tabular-nums text-muted-foreground">{pct}%</span>
          </>
        )}
      </div>
    )
  }

  if (stage === 'failed') {
    return (
      <div className="mt-3 flex items-center gap-3 rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-2.5 text-sm">
        <TriangleAlert className="size-4 text-destructive" />
        <span className="flex-1">Transcription failed.</span>
        {menu('Retry')}
      </div>
    )
  }

  const t = lecture.transcription
  if (stage !== 'done' || !t) {
    return (
      <div className="mt-3 flex items-center gap-3 rounded-lg border bg-card px-4 py-2.5 text-sm">
        <AudioLines className="size-4 text-muted-foreground" />
        <span className="flex-1 text-muted-foreground">Not transcribed yet.</span>
        {menu('Transcribe', <AudioLines />)}
      </div>
    )
  }

  return (
    <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
      <span className="flex items-center gap-1">
        {t.device === 'cuda' ? <Zap className="size-3.5" /> : <Cpu className="size-3.5" />}
        {t.model} on {t.device === 'cuda' ? 'GPU' : 'CPU'}
      </span>
      {t.speed_x && (
        <span>
          {t.speed_x}× real time ({formatDuration(t.elapsed_s)})
        </span>
      )}
      {lecture.language && <span>{LANGS[lecture.language] ?? lecture.language}</span>}
      {t.gpu_fallback_reason && (
        <span className="text-amber-600 dark:text-amber-400" title={t.gpu_fallback_reason}>
          GPU failed, used CPU
        </span>
      )}
      <span className="ml-auto">{menu('Re-transcribe')}</span>
    </div>
  )
}
