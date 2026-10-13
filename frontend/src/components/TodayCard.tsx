import { useQuery } from '@tanstack/react-query'
import { CalendarDays, MapPin } from 'lucide-react'
import { Link } from 'react-router'

import { api } from '@/lib/api'
import type { TimetableEvent } from '@/lib/types'
import { cn } from '@/lib/utils'

const time = (iso: string) =>
  new Date(iso).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
const day = (iso: string) => new Date(iso).toLocaleDateString(undefined, { weekday: 'long' })

/** Dashboard: today's timetable, the running slot highlighted; otherwise the next lecture. */
export function TodayCard() {
  const { data: t } = useQuery({
    queryKey: ['timetable-now'],
    queryFn: api.timetableNow,
    refetchInterval: 60_000,
  })
  if (!t || (!t.today.length && !t.next)) return null
  const now = Date.now()

  return (
    <section className="mt-3 rounded-lg border bg-card p-4">
      <h2 className="flex items-center gap-2 text-sm font-medium">
        <CalendarDays className="size-4" /> Today
      </h2>
      {t.today.length ? (
        <ul className="mt-2 divide-y">
          {t.today.map((e) => (
            <Row key={e.id} e={e} live={t.current?.id === e.id} past={new Date(e.end).getTime() < now} />
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-sm text-muted-foreground">No lectures today.</p>
      )}
      {t.next && !t.today.some((e) => e.id === t.next!.id) && (
        <p className="mt-2 text-xs text-muted-foreground">
          Next: {t.next.subject_name || t.next.summary}, {day(t.next.start)} {time(t.next.start)}
        </p>
      )}
    </section>
  )
}

function Row({ e, live, past }: { e: TimetableEvent; live: boolean; past: boolean }) {
  const name = e.subject_name || e.summary
  return (
    <li className={cn('flex items-center gap-3 py-1.5 text-sm', past && 'text-muted-foreground')}>
      <span className="shrink-0 font-mono text-xs whitespace-nowrap tabular-nums sm:w-36">
        {time(e.start)}–{time(e.end)}
      </span>
      <span
        className="size-2 shrink-0 rounded-full bg-muted-foreground/40"
        style={e.subject_color ? { background: e.subject_color } : undefined}
      />
      <span className="min-w-0 flex-1 truncate" title={e.summary}>
        {e.subject_id ? <Link to={`/subjects/${e.subject_id}`}>{name}</Link> : name}
        {e.kind && <span className="ml-1.5 text-xs text-muted-foreground">{e.kind}</span>}
      </span>
      {e.location && (
        <span className="hidden items-center gap-1 text-xs text-muted-foreground sm:flex">
          <MapPin className="size-3" /> {e.location}
        </span>
      )}
      {live && (
        <span className="rounded-full bg-red-500/10 px-2 py-0.5 text-xs font-medium text-red-600 dark:text-red-400">
          now
        </span>
      )}
    </li>
  )
}
