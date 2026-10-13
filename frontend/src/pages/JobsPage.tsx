import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, CircleDashed, Loader2, RotateCcw, Sparkles, X, XCircle } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { useEvent } from '@/hooks/useEvents'
import { api } from '@/lib/api'
import { formatDuration } from '@/lib/dates'
import type { JobRow } from '@/lib/types'
import { cn } from '@/lib/utils'

const TABS = [
  { value: 'active', label: 'Running & waiting' },
  { value: 'failed', label: 'Failed' },
  { value: 'all', label: 'All' },
]

const ago = (iso: string) => {
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.round(s / 60)} min ago`
  if (s < 86400) return `${Math.round(s / 3600)} h ago`
  return new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

function StatusIcon({ job }: { job: JobRow }) {
  if (job.status === 'running') return <Loader2 className="size-4 animate-spin text-primary" />
  if (job.status === 'queued') return <CircleDashed className="size-4 text-muted-foreground" />
  if (job.status === 'done') return <CheckCircle2 className="size-4 text-emerald-500" />
  if (job.status === 'failed') return <XCircle className="size-4 text-destructive" />
  return <X className="size-4 text-muted-foreground" />
}

export function JobsPage() {
  const qc = useQueryClient()
  const [tab, setTab] = useState('active')
  const { data: jobs = [] } = useQuery({ queryKey: ['jobs', tab], queryFn: () => api.jobs(tab) })
  const { data: pending } = useQuery({ queryKey: ['pending-generation'], queryFn: api.pendingGeneration })
  useEvent('job', () => {
    qc.invalidateQueries({ queryKey: ['jobs'] })
    qc.invalidateQueries({ queryKey: ['job-counts'] })
  })

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['jobs'] })
    qc.invalidateQueries({ queryKey: ['job-counts'] })
    qc.invalidateQueries({ queryKey: ['pending-generation'] })
  }
  const retry = useMutation({
    mutationFn: api.retryJob,
    onSuccess: refresh,
    onError: (e: Error) => toast.error(e.message),
  })
  const cancel = useMutation({
    mutationFn: api.cancelJob,
    onSuccess: refresh,
    onError: (e: Error) => toast.error(e.message),
  })
  const batch = useMutation({
    mutationFn: api.generatePending,
    onSuccess: (r) => {
      toast.success(`Queued study material for ${r.queued} lecture(s)`)
      refresh()
      setTab('active')
    },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <div className="mx-auto max-w-4xl px-4 sm:px-8 py-10">
      <h1 className="text-2xl font-semibold tracking-tight">Jobs</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        Background work: saving recordings, imports, transcription, study material and search indexing. Jobs
        run one at a time and continue after a restart.
      </p>

      {pending && pending.count > 0 && (
        <div className="mt-6 flex items-center gap-3 rounded-lg border bg-card px-4 py-3 text-sm">
          <Sparkles className="size-4 text-primary" />
          <span className="flex-1">
            {pending.count} transcribed lecture{pending.count === 1 ? ' has' : 's have'} no study material
            yet.
          </span>
          {pending.configured ? (
            <Button size="sm" onClick={() => batch.mutate()} disabled={batch.isPending}>
              Generate all{pending.usd > 0 ? ` (≈ $${pending.usd.toFixed(2)})` : ''}
            </Button>
          ) : (
            <Button size="sm" variant="outline" asChild>
              <Link to="/settings">Add API key</Link>
            </Button>
          )}
        </div>
      )}

      <div className="mt-6 flex gap-1 border-b">
        {TABS.map((t) => (
          <button
            key={t.value}
            onClick={() => setTab(t.value)}
            className={cn(
              '-mb-px border-b-2 px-3 py-2 text-sm',
              tab === t.value ? 'border-primary font-medium' : 'border-transparent text-muted-foreground',
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {jobs.length === 0 ? (
        <p className="py-8 text-center text-sm text-muted-foreground">
          {tab === 'active' ? 'Nothing running.' : tab === 'failed' ? 'No failed jobs.' : 'No jobs yet.'}
        </p>
      ) : (
        <ul className="divide-y">
          {jobs.map((j) => (
            <li key={j.id} className="flex items-start gap-3 py-3">
              <span className="mt-0.5">
                <StatusIcon job={j} />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2 text-sm">
                  <span className="font-medium">{j.label}</span>
                  {j.lecture_id && j.lecture_title ? (
                    <Link
                      to={`/lectures/${j.lecture_id}`}
                      className="truncate text-muted-foreground hover:underline"
                    >
                      {j.subject_name ? `${j.subject_name} · ` : ''}
                      {j.lecture_title}
                    </Link>
                  ) : j.lecture_id ? (
                    <span className="text-muted-foreground">(deleted lecture)</span>
                  ) : null}
                </div>
                {j.status === 'running' && (
                  <div className="mt-1.5 flex items-center gap-2 text-xs text-muted-foreground">
                    <div className="h-1.5 w-48 overflow-hidden rounded-full bg-muted">
                      <div
                        className="h-full bg-primary transition-[width]"
                        style={{ width: `${Math.max(j.progress * 100, 3)}%` }}
                      />
                    </div>
                    {j.message}
                  </div>
                )}
                {j.status === 'failed' && j.error && (
                  <p className="mt-1 text-xs break-words text-destructive">{j.error}</p>
                )}
                <div className="mt-0.5 text-xs text-muted-foreground">
                  {ago(j.created_at)}
                  {j.duration_s != null &&
                    j.status !== 'running' &&
                    ` · took ${formatDuration(j.duration_s)}`}
                  {j.attempts > 1 && ` · attempt ${j.attempts}`}
                </div>
              </div>
              {(j.status === 'failed' || j.status === 'cancelled') && (
                <Button
                  size="xs"
                  variant="outline"
                  onClick={() => retry.mutate(j.id)}
                  disabled={retry.isPending}
                >
                  <RotateCcw /> Retry
                </Button>
              )}
              {j.status === 'queued' && (
                <Button size="xs" variant="ghost" onClick={() => cancel.mutate(j.id)}>
                  Cancel
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
