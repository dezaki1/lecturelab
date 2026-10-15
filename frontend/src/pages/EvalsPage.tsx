import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, FlaskConical, Loader2, Pencil, Play, Plus, X } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useEvent } from '@/hooks/useEvents'
import { api } from '@/lib/api'
import { formatDateTime } from '@/lib/dates'
import type { EvalCase, EvalRunRow } from '@/lib/types'
import { cn } from '@/lib/utils'

const SHOWN: Record<string, string[]> = {
  retrieval: ['hit@1', 'hit@5', 'hit@10', 'mrr', 'lecture_hit@5'],
  grounding: ['supported', 'partly', 'unsupported', 'bad_ref'],
}
const LOWER_IS_BETTER = new Set(['partly', 'unsupported', 'bad_ref'])

/** Evals: hand-check search questions, run retrieval/grounding evals, compare runs. */
export function EvalsPage() {
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['evals'],
    queryFn: api.evals,
    refetchInterval: (q) => (q.state.data?.runs.some((r) => r.status === 'running') ? 2000 : false),
  })
  useEvent('eval', () => qc.invalidateQueries({ queryKey: ['evals'] }))
  const refresh = () => qc.invalidateQueries({ queryKey: ['evals'] })
  const onError = (e: Error) => toast.error(e.message)
  const make = useMutation({
    mutationFn: () => api.makeEvalCases(20),
    onSuccess: (r) => {
      refresh()
      toast.success(r.made ? `${r.made} new questions to check` : 'No unused note sections left')
    },
    onError,
  })
  const run = useMutation({ mutationFn: api.runEval, onSuccess: refresh, onError })
  if (!data) return null
  const candidates = data.cases.filter((c) => c.status === 'candidate')
  const approved = data.cases.filter((c) => c.status === 'approved')

  return (
    <div className="mx-auto max-w-5xl px-4 py-10 sm:px-8">
      <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
        <FlaskConical className="size-6" /> Evals
      </h1>
      <p className="mt-1 text-sm text-muted-foreground">
        Measure how well search finds the right lecture moment and whether generated cards and questions are
        backed by the lecture. Rerun after changing prompts or search settings and compare.
      </p>

      <div className="mt-6 flex flex-wrap items-center gap-2">
        <Button
          variant="outline"
          onClick={() => run.mutate('retrieval')}
          disabled={!approved.length || run.isPending}
          title={approved.length ? undefined : 'Approve some questions first'}
        >
          <Play /> Run search eval ({approved.length} questions, free)
        </Button>
        <Button variant="outline" onClick={() => run.mutate('grounding')} disabled={run.isPending}>
          <Play /> Run grounding eval (40 items, ~$0.01)
        </Button>
        <span className="ml-auto text-xs text-muted-foreground">
          or from a terminal: <code>lecturelab eval retrieval</code>
        </span>
      </div>

      <Runs runs={data.runs} />

      <section className="mt-8">
        <div className="flex flex-wrap items-baseline gap-2">
          <h2 className="font-semibold">Search questions</h2>
          <span className="text-sm text-muted-foreground">
            {data.counts.approved} approved · {data.counts.candidate} to check · {data.counts.rejected}{' '}
            rejected
          </span>
          <Button
            size="sm"
            variant="outline"
            className="ml-auto"
            onClick={() => make.mutate()}
            disabled={make.isPending}
          >
            {make.isPending ? <Loader2 className="animate-spin" /> : <Plus />} Make 20 more from my notes
          </Button>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          Each question is made from a section of your notes; the section&apos;s place in the lecture is the
          right answer. Approve the ones a student would really ask, fix the wording, reject the rest.
        </p>
        <ul className="mt-3 space-y-2">
          {[...candidates, ...approved].map((c) => (
            <CaseRow key={c.id} c={c} onChange={refresh} />
          ))}
        </ul>
      </section>
    </div>
  )
}

function Runs({ runs }: { runs: EvalRunRow[] }) {
  if (!runs.length) return null
  return (
    <section className="mt-6 overflow-x-auto rounded-lg border bg-card">
      <table className="w-full min-w-[640px] text-sm">
        <thead className="text-left text-xs text-muted-foreground">
          <tr className="border-b">
            <th className="px-3 py-2 font-normal">When</th>
            <th className="px-3 py-2 font-normal">Eval</th>
            <th className="px-3 py-2 font-normal">Result</th>
            <th className="px-3 py-2 font-normal">Changed since the previous run of this eval</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {runs.map((r) => {
            const prev = runs.find((x) => x.kind === r.kind && x.id < r.id && x.status === 'done')
            return (
              <tr key={r.id} className="align-top">
                <td className="px-3 py-2 whitespace-nowrap text-muted-foreground">
                  {formatDateTime(r.created_at)}
                </td>
                <td className="px-3 py-2">
                  <Badge variant="outline">{r.kind === 'retrieval' ? 'Search' : 'Grounding'}</Badge>
                </td>
                <td className="px-3 py-2">
                  {r.status === 'running' ? (
                    <span className="flex items-center gap-1 text-muted-foreground">
                      <Loader2 className="size-3.5 animate-spin" /> running
                    </span>
                  ) : r.status === 'failed' ? (
                    <span className="text-destructive">{r.error}</span>
                  ) : (
                    <div className="flex flex-wrap gap-x-3 gap-y-1 tabular-nums">
                      {SHOWN[r.kind].map((k) => (
                        <Metric key={k} name={k} value={r.metrics[k]} prev={prev?.metrics[k]} />
                      ))}
                      <span className="text-xs text-muted-foreground">n={r.metrics.n}</span>
                    </div>
                  )}
                </td>
                <td className="px-3 py-2 text-xs text-muted-foreground">{promptDiff(r, prev)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </section>
  )
}

function Metric({ name, value, prev }: { name: string; value?: number; prev?: number }) {
  if (value == null) return null
  const pct = name === 'mrr' ? value.toFixed(2) : `${Math.round(value * 100)}%`
  const delta = prev == null ? 0 : value - prev
  const better = LOWER_IS_BETTER.has(name) ? delta < 0 : delta > 0
  return (
    <span>
      <span className="text-xs text-muted-foreground">{name} </span>
      {pct}
      {Math.abs(delta) >= 0.005 && (
        <span
          className={cn('ml-0.5 text-xs', better ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-500')}
        >
          {delta > 0 ? '+' : ''}
          {name === 'mrr' ? delta.toFixed(2) : Math.round(delta * 100)}
        </span>
      )}
    </span>
  )
}

function promptDiff(r: EvalRunRow, prev?: EvalRunRow): string {
  if (!prev) return 'first run'
  const a = prev.config.prompts ?? {}
  const b = r.config.prompts ?? {}
  const changed = Object.keys(b).filter((k) => a[k] !== b[k])
  const parts = changed.map((k) => `${k} v${a[k] ?? '–'}→v${b[k]}`)
  if (prev.config.provider !== r.config.provider)
    parts.push(`provider ${prev.config.provider}→${r.config.provider}`)
  return parts.join(', ') || 'no prompt changes'
}

function CaseRow({ c, onChange }: { c: EvalCase; onChange: () => void }) {
  const [editing, setEditing] = useState(false)
  const [q, setQ] = useState(c.question)
  const patch = useMutation({
    mutationFn: (body: { status?: string; question?: string }) => api.patchEvalCase(c.id, body),
    onSuccess: () => {
      setEditing(false)
      onChange()
    },
    onError: (e: Error) => toast.error(e.message),
  })
  return (
    <li
      className={cn(
        'rounded-lg border bg-card p-3 text-sm',
        c.status === 'candidate' && 'border-amber-500/40',
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        {editing ? (
          <form
            className="flex min-w-0 flex-1 gap-1"
            onSubmit={(e) => {
              e.preventDefault()
              patch.mutate({ question: q })
            }}
          >
            <Input value={q} onChange={(e) => setQ(e.target.value)} className="h-8" aria-label="Question" />
            <Button size="sm" type="submit">
              Save
            </Button>
          </form>
        ) : (
          <span className="min-w-0 flex-1 font-medium">{c.question}</span>
        )}
        {c.status === 'approved' && <Badge variant="secondary">approved</Badge>}
        {!editing && (
          <Button size="icon-xs" variant="ghost" aria-label="Edit question" onClick={() => setEditing(true)}>
            <Pencil />
          </Button>
        )}
        {c.status !== 'approved' && (
          <Button size="xs" onClick={() => patch.mutate({ status: 'approved' })}>
            <Check /> Approve
          </Button>
        )}
        <Button size="xs" variant="ghost" onClick={() => patch.mutate({ status: 'rejected' })}>
          <X /> Reject
        </Button>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        Answer:{' '}
        <Link className="hover:underline" to={`/lectures/${c.lecture_id}?t=${Math.floor(c.gold_start_s)}`}>
          {c.lecture_title} {c.gold}
        </Link>{' '}
        ({c.section}) · “{c.passage.slice(0, 220)}
        {c.passage.length > 220 ? '…' : ''}”
      </p>
    </li>
  )
}
