import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ExternalLink, Flame, PartyPopper, Undo2 } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { toast } from 'sonner'

import { Markdown } from '@/components/Markdown'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import type { ReviewNext } from '@/lib/types'
import { cn } from '@/lib/utils'

const RATINGS = [
  { value: 1, label: 'Again', key: '1', cls: 'border-red-500/40 hover:bg-red-500/10' },
  { value: 2, label: 'Hard', key: '2', cls: 'border-amber-500/40 hover:bg-amber-500/10' },
  { value: 3, label: 'Good', key: '3', cls: 'border-emerald-500/40 hover:bg-emerald-500/10' },
  { value: 4, label: 'Easy', key: '4', cls: 'border-sky-500/40 hover:bg-sky-500/10' },
]

export function formatInterval(seconds: number): string {
  const m = seconds / 60
  if (m < 1) return '<1m'
  if (m < 60) return `${Math.round(m)}m`
  const h = m / 60
  if (h < 24) return `${Math.round(h)}h`
  const d = h / 24
  if (d < 30) return `${Math.round(d)}d`
  if (d < 365) return `${Math.round(d / 30)}mo`
  return `${(d / 365).toFixed(1)}y`
}

/** "{{c1::answer}}" -> hidden "[…]" or bold answer. */
function cloze(text: string, reveal: boolean) {
  return text.replace(/\{\{c\d+::(.+?)(?:::(.+?))?\}\}/g, (_, ans: string, hint?: string) =>
    reveal ? `**${ans}**` : `\\[${hint ?? '…'}\\]`,
  )
}

function sides(r: ReviewNext) {
  const c = r.card!
  if (c.type === 'cloze') return { front: cloze(c.front, false), back: cloze(c.front, true), extra: c.back }
  if (r.reverse) return { front: c.back, back: c.front, extra: '' }
  return { front: c.front, back: c.back, extra: '' }
}

export function ReviewPage() {
  const qc = useQueryClient()
  const [params] = useSearchParams()
  const subjectId = params.get('subject') ? Number(params.get('subject')) : undefined
  const [revealed, setRevealed] = useState(false)
  const shownAt = useRef(Date.now())

  const { data: next, isFetching } = useQuery({
    queryKey: ['review-next', subjectId],
    queryFn: () => api.reviewNext(subjectId),
    refetchOnWindowFocus: true,
  })
  const { data: summary } = useQuery({ queryKey: ['review-summary'], queryFn: api.reviewSummary })

  const refresh = useCallback(() => {
    setRevealed(false)
    shownAt.current = Date.now()
    qc.invalidateQueries({ queryKey: ['review-next'] })
    qc.invalidateQueries({ queryKey: ['review-summary'] })
  }, [qc])

  const rate = useMutation({
    mutationFn: (rating: number) => api.reviewCard(next!.card!.id, rating, Date.now() - shownAt.current),
    onSuccess: refresh,
    onError: (e: Error) => toast.error(e.message),
  })
  const undo = useMutation({
    mutationFn: api.reviewUndo,
    onSuccess: refresh,
    onError: (e: Error) => toast.info(e.message),
  })
  const suspend = useMutation({
    mutationFn: () => api.updateCard(next!.card!.id, { suspended: true }),
    onSuccess: () => {
      toast('Card suspended')
      refresh()
    },
  })
  const extra = useMutation({ mutationFn: () => api.reviewExtraNew(10), onSuccess: refresh })

  // When nothing is due but cards are in their learning steps, check back when the next one is.
  useEffect(() => {
    if (next?.card || !next?.remaining.next_due) return
    const wait = new Date(next.remaining.next_due).getTime() - Date.now()
    if (wait > 20 * 60_000) return
    const id = setTimeout(refresh, Math.max(wait, 1000) + 500)
    return () => clearTimeout(id)
  }, [next, refresh])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || e.ctrlKey || e.metaKey || e.altKey) return
      if (!next?.card || rate.isPending) {
        if (e.key.toLowerCase() === 'u') undo.mutate()
        return
      }
      if (e.key === ' ' || e.key === 'Enter') {
        e.preventDefault()
        if (!revealed) setRevealed(true)
        else rate.mutate(3) // Space on the answer side = Good
      } else if (revealed && ['1', '2', '3', '4'].includes(e.key)) {
        rate.mutate(Number(e.key))
      } else if (e.key.toLowerCase() === 'u') {
        undo.mutate()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [next, revealed, rate, undo])

  if (!next) return null
  const remaining = next.remaining

  if (!next.card) {
    return (
      <div className="mx-auto max-w-xl px-8 py-16 text-center">
        <PartyPopper className="mx-auto size-10 text-primary" />
        <h1 className="mt-4 text-2xl font-semibold">All done for now</h1>
        {summary && (
          <p className="mt-2 text-sm text-muted-foreground">
            {summary.reviewed_today} reviews today ·{' '}
            <span className="inline-flex items-center gap-1">
              <Flame className="size-3.5 text-orange-500" /> {summary.streak}-day streak
            </span>
          </p>
        )}
        {remaining.next_due && (
          <p className="mt-2 text-sm text-muted-foreground">
            Next card due{' '}
            {new Date(remaining.next_due).toLocaleString(undefined, {
              weekday: 'short',
              hour: '2-digit',
              minute: '2-digit',
            })}
          </p>
        )}
        <div className="mt-6 flex justify-center gap-2">
          {remaining.new_total > 0 && (
            <Button variant="outline" onClick={() => extra.mutate()} disabled={extra.isPending}>
              Learn 10 more new cards ({remaining.new_total} waiting)
            </Button>
          )}
          <Button variant="ghost" onClick={() => undo.mutate()}>
            <Undo2 /> Undo last
          </Button>
        </div>
      </div>
    )
  }

  const { front, back, extra: backExtra } = sides(next)
  return (
    <div className="mx-auto flex min-h-full max-w-2xl flex-col px-8 py-8">
      <header className="flex items-center gap-3 text-sm text-muted-foreground">
        <span className="size-2.5 rounded-full" style={{ background: next.subject_color }} />
        <span className="truncate">{next.subject_name}</span>
        {next.lecture_number && (
          <a
            href={`/lectures/${next.lecture_id}${next.card.source_refs[0]?.start_s != null ? `?t=${Math.floor(next.card.source_refs[0].start_s)}` : ''}`}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 hover:text-foreground"
            title="Open the lecture at this card's source (new tab)"
          >
            L{next.lecture_number} <ExternalLink className="size-3" />
          </a>
        )}
        {next.is_new && (
          <span className="rounded bg-sky-500/15 px-1.5 text-xs text-sky-700 dark:text-sky-300">new</span>
        )}
        <span className="ml-auto tabular-nums">
          <span className="text-red-600 dark:text-red-400">{remaining.due} due</span> ·{' '}
          <span className="text-sky-600 dark:text-sky-400">{remaining.new} new</span>
        </span>
      </header>

      <div
        className={cn(
          'mt-6 flex flex-1 flex-col rounded-xl border bg-card p-8 shadow-sm transition-opacity',
          isFetching && 'opacity-70',
        )}
      >
        <Markdown className="text-lg">{front}</Markdown>
        {revealed && (
          <div className="mt-6 border-t pt-6">
            <Markdown className="text-lg">{back}</Markdown>
            {backExtra && (
              <div className="mt-3 text-sm text-muted-foreground">
                <Markdown>{backExtra}</Markdown>
              </div>
            )}
          </div>
        )}
      </div>

      <footer className="mt-6">
        {!revealed ? (
          <Button className="w-full" size="lg" onClick={() => setRevealed(true)}>
            Show answer <kbd className="ml-2 rounded border px-1.5 text-xs opacity-70">Space</kbd>
          </Button>
        ) : (
          <div className="grid grid-cols-4 gap-2">
            {RATINGS.map((r) => (
              <button
                key={r.value}
                disabled={rate.isPending}
                onClick={() => rate.mutate(r.value)}
                className={cn('flex flex-col items-center rounded-lg border py-2 text-sm transition', r.cls)}
              >
                <span className="font-medium">{r.label}</span>
                <span className="text-xs text-muted-foreground">
                  {next.intervals ? formatInterval(next.intervals[r.value]) : ''} · {r.key}
                </span>
              </button>
            ))}
          </div>
        )}
        <div className="mt-3 flex justify-center gap-4 text-xs text-muted-foreground">
          <button className="hover:text-foreground" onClick={() => undo.mutate()}>
            Undo (U)
          </button>
          <button className="hover:text-foreground" onClick={() => suspend.mutate()}>
            Suspend card
          </button>
        </div>
      </footer>
    </div>
  )
}
