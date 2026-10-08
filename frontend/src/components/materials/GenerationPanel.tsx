import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { KeyRound, Loader2, Sparkles, TriangleAlert } from 'lucide-react'
import { Link } from 'react-router'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import type { LectureDetail } from '@/lib/types'

/** Status of the study-material stage: generate (with cost estimate), progress, or retry. */
export function GenerationPanel({ lecture }: { lecture: LectureDetail }) {
  const qc = useQueryClient()
  const stage = lecture.stages.generate
  const transcribed = lecture.stages.transcribe === 'done'
  const { data: est } = useQuery({
    queryKey: ['estimate', lecture.id],
    queryFn: () => api.estimate(lecture.id),
    enabled: transcribed && stage !== 'done' && stage !== 'running' && stage !== 'pending',
  })
  const run = useMutation({
    mutationFn: () => api.generate(lecture.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['lecture', lecture.id] }),
    onError: (e: Error) => toast.error(e.message),
  })
  if (!transcribed) return null

  if (stage === 'pending' || stage === 'running') {
    const pct = lecture.job_progress != null ? Math.round(lecture.job_progress * 100) : 0
    return (
      <div className="mt-3 flex items-center gap-3 rounded-lg border bg-card px-4 py-2.5 text-sm">
        <Loader2 className="size-4 animate-spin text-primary" />
        <span>
          {stage === 'pending' ? 'Waiting to generate study material…' : lecture.job_message || 'Generating…'}
        </span>
        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
          <div className="h-full bg-primary transition-[width]" style={{ width: `${Math.max(pct, 3)}%` }} />
        </div>
      </div>
    )
  }

  const cost = est && est.usd > 0 ? ` (≈ $${est.usd.toFixed(2)})` : ''
  if (stage === 'failed') {
    return (
      <div className="mt-3 flex items-start gap-3 rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-2.5 text-sm">
        <TriangleAlert className="mt-0.5 size-4 shrink-0 text-destructive" />
        <span className="flex-1">{lecture.error ?? 'Generating study material failed.'}</span>
        <Button size="xs" variant="outline" onClick={() => run.mutate()} disabled={run.isPending}>
          Retry all{cost}
        </Button>
      </div>
    )
  }

  if (stage !== 'done') {
    if (est && !est.configured) {
      return (
        <div className="mt-3 flex items-center gap-3 rounded-lg border bg-card px-4 py-2.5 text-sm">
          <KeyRound className="size-4 text-muted-foreground" />
          <span className="flex-1 text-muted-foreground">
            Add your Anthropic API key to generate summaries, notes, flashcards and questions.
          </span>
          <Button size="xs" variant="outline" asChild>
            <Link to="/settings">Open settings</Link>
          </Button>
        </div>
      )
    }
    return (
      <div className="mt-3 flex items-center gap-3 rounded-lg border bg-card px-4 py-2.5 text-sm">
        <Sparkles className="size-4 text-primary" />
        <span className="flex-1 text-muted-foreground">No study material yet.</span>
        <Button size="sm" onClick={() => run.mutate()} disabled={run.isPending}>
          Generate study material{cost}
        </Button>
      </div>
    )
  }
  return null
}
