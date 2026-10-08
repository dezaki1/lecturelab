import { useQuery } from '@tanstack/react-query'
import { Coins } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'

import { api } from '@/lib/api'
import { formatDuration } from '@/lib/dates'
import type { Usage } from '@/lib/types'
import { cn } from '@/lib/utils'

const TASK_LABEL: Record<string, string> = {
  summary: 'Summary',
  notes: 'Study notes',
  glossary: 'Glossary',
  announcements: 'Announcements',
  flashcards: 'Flashcards',
  exercises: 'Exercises',
  questions: 'Exam questions',
  verify: 'Fact check',
  chat: 'Chat',
  query_rewrite: 'Chat query rewrite',
  test: 'Connection test',
}

const usd = (v: number) => (v >= 1 ? `$${v.toFixed(2)}` : v >= 0.01 ? `$${v.toFixed(3)}` : `$${v.toFixed(4)}`)
const tokens = (n: number) =>
  n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}k` : String(n)

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums">{value}</div>
      {sub && <div className="mt-0.5 text-xs text-muted-foreground">{sub}</div>}
    </div>
  )
}

/** Daily cost: one series, single hue, bars anchored to the baseline with a hover tooltip. */
function DailyCost({ data }: { data: Usage['by_day'] }) {
  const [hover, setHover] = useState<number | null>(null)
  const max = Math.max(...data.map((d) => d.cost), 0.0001)
  const H = 140
  const fmt = (d: string) =>
    new Date(d + 'T00:00').toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
  return (
    <figure className="rounded-lg border bg-card p-4">
      <figcaption className="mb-3 text-sm font-medium">Cost per day</figcaption>
      <div className="relative">
        <div className="absolute top-0 left-0 text-[11px] tabular-nums text-muted-foreground">{usd(max)}</div>
        <div className="flex items-end gap-[2px] border-b border-border pt-5" style={{ height: H + 20 }}>
          {data.map((d, i) => (
            <div
              key={d.day}
              className="group relative flex h-full flex-1 items-end"
              onMouseEnter={() => setHover(i)}
              onMouseLeave={() => setHover(null)}
            >
              <div
                className={cn(
                  'w-full rounded-t-[4px] bg-primary/70 transition-colors',
                  hover === i && 'bg-primary',
                )}
                style={{ height: d.cost > 0 ? Math.max(2, (d.cost / max) * H) : 0 }}
              />
              {hover === i && (
                <div className="pointer-events-none absolute bottom-full left-1/2 z-10 mb-1 -translate-x-1/2 rounded-md border bg-popover px-2 py-1 text-xs whitespace-nowrap shadow-sm">
                  <div className="text-muted-foreground">{fmt(d.day)}</div>
                  <div className="font-medium tabular-nums">{usd(d.cost)}</div>
                </div>
              )}
            </div>
          ))}
        </div>
        <div className="mt-1 flex justify-between text-[11px] text-muted-foreground">
          <span>{fmt(data[0].day)}</span>
          <span>{fmt(data[data.length - 1].day)}</span>
        </div>
      </div>
    </figure>
  )
}

function Table({
  title,
  head,
  rows,
}: {
  title: string
  head: string[]
  rows: (string | number | React.ReactNode)[][]
}) {
  return (
    <section className="rounded-lg border bg-card">
      <h2 className="border-b px-4 py-2.5 text-sm font-medium">{title}</h2>
      {rows.length === 0 ? (
        <p className="px-4 py-3 text-sm text-muted-foreground">Nothing yet.</p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-muted-foreground">
              {head.map((h, i) => (
                <th key={h} className={cn('px-4 py-2 font-normal', i === 0 ? 'text-left' : 'text-right')}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-t">
                {r.map((c, j) => (
                  <td key={j} className={cn('px-4 py-2', j === 0 ? 'text-left' : 'text-right tabular-nums')}>
                    {c}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}

export function UsagePage() {
  const [days, setDays] = useState(30)
  const { data: u } = useQuery({ queryKey: ['usage', days], queryFn: () => api.usage(days) })
  if (!u) return null
  const generated = u.by_lecture.filter((l) => l.duration_s)
  const perHour = generated.length
    ? generated.reduce((s, l) => s + l.cost, 0) /
      (generated.reduce((s, l) => s + (l.duration_s ?? 0), 0) / 3600)
    : null

  return (
    <div className="mx-auto max-w-5xl space-y-6 px-8 py-10">
      <header className="flex items-center gap-3">
        <Coins className="size-5 text-primary" />
        <h1 className="text-2xl font-semibold tracking-tight">Usage & costs</h1>
        <div className="ml-auto flex rounded-md border p-0.5 text-sm">
          {[7, 30, 90].map((d) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={cn(
                'rounded px-2.5 py-1',
                d === days ? 'bg-muted font-medium' : 'text-muted-foreground',
              )}
            >
              {d} days
            </button>
          ))}
        </div>
      </header>
      <p className="-mt-3 text-sm text-muted-foreground">
        Estimated from token counts at Anthropic list prices. Transcription, search and Ollama are local and
        free.
      </p>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label={`Last ${days} days`} value={usd(u.total_cost)} sub={`${u.calls} AI calls`} />
        <Stat label="All time" value={usd(u.all_time_cost)} />
        <Stat
          label="Per lecture hour"
          value={perHour != null ? usd(perHour) : '–'}
          sub={generated.length ? `over ${generated.length} lecture(s)` : undefined}
        />
        <Stat label="Saved by caching" value={usd(u.cache_savings)} sub="transcript reused across calls" />
      </div>

      <DailyCost data={u.by_day} />

      <div className="grid gap-6 lg:grid-cols-2">
        <Table
          title="By task"
          head={['Task', 'Calls', 'Input', 'Output', 'Cache read', 'Cost']}
          rows={u.by_task.map((t) => [
            TASK_LABEL[t.task] ?? t.task,
            t.calls,
            tokens(t.input + t.cache_write),
            tokens(t.output),
            tokens(t.cache_read),
            usd(t.cost),
          ])}
        />
        <Table
          title="By model"
          head={['Model', 'Calls', 'Tokens', 'Cost']}
          rows={u.by_model.map((m) => [
            <code key="m">{m.model}</code>,
            m.calls,
            tokens(m.input + m.output + m.cache_read + m.cache_write),
            usd(m.cost),
          ])}
        />
      </div>

      <Table
        title="By subject"
        head={['Subject', 'Cost']}
        rows={u.by_subject.map((s) => [
          <span key="s" className="flex items-center gap-2">
            <span className="size-2 rounded-full" style={{ background: s.color }} />
            {s.name}
          </span>,
          usd(s.cost),
        ])}
      />

      <Table
        title="By lecture"
        head={['Lecture', 'Length', 'Calls', 'Cost']}
        rows={u.by_lecture.map((l) => [
          <Link key="l" to={`/lectures/${l.lecture_id}`} className="hover:underline">
            <span className="text-muted-foreground">
              {l.subject} {l.number ? `L${l.number}` : ''}
            </span>{' '}
            {l.title}
          </Link>,
          l.duration_s ? formatDuration(l.duration_s) : '–',
          l.calls,
          usd(l.cost),
        ])}
      />
    </div>
  )
}
