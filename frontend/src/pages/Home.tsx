import { useQuery } from '@tanstack/react-query'
import { CalendarClock, Flame, Layers, Target } from 'lucide-react'
import { Link } from 'react-router'

import { WeakSpotsCard } from '@/components/hub/WeakSpots'
import { MoodleReview } from '@/components/MoodleWatch'
import { PlanTodayCard } from '@/components/PlanTodayCard'
import { TodayCard } from '@/components/TodayCard'
import { Button } from '@/components/ui/button'
import { useEvent } from '@/hooks/useEvents'
import { api } from '@/lib/api'
import type { SubjectProgress } from '@/lib/types'
import { cn } from '@/lib/utils'

const pct = (x: number | null | undefined) => (x == null ? '—' : `${Math.round(x * 100)}%`)

function weekday(iso: string, short = true) {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(y, m - 1, d).toLocaleDateString(
    undefined,
    short ? { weekday: 'short' } : { day: 'numeric', month: 'short' },
  )
}

export function Home() {
  const { data: d, refetch } = useQuery({ queryKey: ['dashboard'], queryFn: api.dashboard })
  const { data: system } = useQuery({ queryKey: ['system'], queryFn: api.system })
  useEvent('review', () => void refetch())

  if (!d) return null
  const toDo = d.due + d.new
  const nextExam = d.subjects.find((s) => s.days_left != null && s.days_left >= 0)

  return (
    <div className="mx-auto max-w-5xl px-4 sm:px-8 py-10">
      <h1 className="text-2xl font-semibold tracking-tight">Overview</h1>

      <div className="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Tile icon={Layers} label="To review today">
          <div className="flex items-end justify-between gap-2">
            <div>
              <div className="text-3xl font-semibold tabular-nums">{toDo}</div>
              <div className="text-xs text-muted-foreground">
                {d.due} due · {d.new} new
              </div>
            </div>
            {toDo > 0 && (
              <Button size="sm" asChild>
                <Link to="/review">Start</Link>
              </Button>
            )}
          </div>
        </Tile>
        <Tile icon={Flame} label="Streak">
          <div className="text-3xl font-semibold tabular-nums">
            {d.streak}{' '}
            <span className="text-base font-normal text-muted-foreground">
              day{d.streak === 1 ? '' : 's'}
            </span>
          </div>
          <div className="text-xs text-muted-foreground">{d.reviewed_today} reviews today</div>
        </Tile>
        <Tile icon={Target} label="Retention, last 30 days">
          <div className="text-3xl font-semibold tabular-nums">{pct(d.retention.rate)}</div>
          <div className="text-xs text-muted-foreground">
            {d.retention.reviews
              ? `of ${d.retention.reviews} mature reviews · target ${pct(d.desired_retention)}`
              : 'shown once cards graduate from learning'}
          </div>
        </Tile>
        <Tile icon={CalendarClock} label="Next exam">
          {nextExam ? (
            <>
              <div className="text-3xl font-semibold tabular-nums">
                {nextExam.days_left}{' '}
                <span className="text-base font-normal text-muted-foreground">
                  day{nextExam.days_left === 1 ? '' : 's'}
                </span>
              </div>
              <div className="truncate text-xs text-muted-foreground" title={nextExam.name}>
                {nextExam.name} · {weekday(nextExam.exam_date!, false)}
              </div>
            </>
          ) : (
            <div className="text-sm text-muted-foreground">
              Add exam dates on the subject pages to see a countdown.
            </div>
          )}
        </Tile>
      </div>

      <MoodleReview compact />
      <TodayCard />
      <PlanTodayCard />

      <div className="mt-3 grid gap-3 lg:grid-cols-[2fr_1fr]">
        <section className="rounded-lg border bg-card p-4">
          <h2 className="text-sm font-medium">Reviews, last 30 days</h2>
          <Bars
            data={d.activity.map((a) => ({
              key: a.date,
              value: a.reviews,
              label: `${weekday(a.date, false)}: ${a.reviews} reviews${a.again ? `, ${a.again} forgotten` : ''}`,
            }))}
          />
        </section>
        <section className="rounded-lg border bg-card p-4">
          <h2 className="text-sm font-medium">Due in the next 7 days</h2>
          <Bars
            data={d.forecast.map((f, i) => ({
              key: f.date,
              value: f.due,
              label: `${i === 0 ? 'Today (incl. overdue)' : weekday(f.date, false)}: ${f.due} cards`,
              tick: i === 0 ? 'today' : weekday(f.date),
            }))}
            ticks
          />
        </section>
      </div>

      <WeakSpotsCard />

      <section className="mt-3 rounded-lg border bg-card">
        <div className="flex items-baseline justify-between px-4 pt-4">
          <h2 className="text-sm font-medium">Subjects</h2>
          <span className="text-xs text-muted-foreground">
            mastery = how much of the deck you would recall today
          </span>
        </div>
        <div className="mt-2 overflow-x-auto">
          <table className="w-full min-w-[640px] text-sm">
            <thead className="text-left text-xs text-muted-foreground">
              <tr className="border-b">
                <th className="px-4 py-2 font-normal">Subject</th>
                <th className="px-2 py-2 font-normal">Exam</th>
                <th className="px-2 py-2 font-normal">Lectures</th>
                <th className="px-2 py-2 font-normal">Cards</th>
                <th className="w-40 px-2 py-2 font-normal">Mastery</th>
                <th className="px-2 py-2 text-right font-normal">Due</th>
                <th className="px-4 py-2 text-right font-normal">Practice</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {d.subjects.map((s) => (
                <SubjectRow key={s.subject_id} s={s} />
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {system && (
        <p className="mt-6 text-xs text-muted-foreground">
          Data in {system.data_dir} · {system.free_gb} GB free
        </p>
      )}
    </div>
  )
}

function Tile({
  icon: Icon,
  label,
  children,
}: {
  icon: typeof Layers
  label: string
  children: React.ReactNode
}) {
  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="mb-2 flex items-center gap-1.5 text-xs text-muted-foreground">
        <Icon className="size-3.5" /> {label}
      </div>
      {children}
    </div>
  )
}

function Bars({
  data,
  ticks = false,
}: {
  data: { key: string; value: number; label: string; tick?: string }[]
  ticks?: boolean
}) {
  const max = Math.max(1, ...data.map((x) => x.value))
  return (
    <div className="mt-3">
      <div className="flex h-28 items-end gap-[3px] border-b">
        {data.map((x) => (
          <div key={x.key} className="group relative flex h-full flex-1 items-end" title={x.label}>
            <div
              className={cn(
                'w-full rounded-t-sm bg-primary/70 transition group-hover:bg-primary',
                !x.value && 'bg-transparent',
              )}
              style={{ height: `${(x.value / max) * 100}%`, minHeight: x.value ? 2 : 0 }}
            />
          </div>
        ))}
      </div>
      {ticks && (
        <div className="mt-1 flex gap-[3px] text-[10px] text-muted-foreground">
          {data.map((x) => (
            <span key={x.key} className="flex-1 text-center">
              {x.tick}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

function SubjectRow({ s }: { s: SubjectProgress }) {
  const total = s.new + s.young + s.mature
  const seg = (n: number) => `${total ? (n / total) * 100 : 0}%`
  const practice = s.practice.max ? s.practice.score / s.practice.max : null
  return (
    <tr className="hover:bg-muted/40">
      <td className="max-w-56 px-4 py-2.5">
        <Link to={`/subjects/${s.subject_id}`} className="flex items-center gap-2 hover:underline">
          <span className="size-2.5 shrink-0 rounded-full" style={{ background: s.color }} />
          <span className="truncate">{s.name}</span>
        </Link>
      </td>
      <td className="px-2 py-2.5 whitespace-nowrap text-muted-foreground">
        {s.days_left == null ? (
          '—'
        ) : s.days_left < 0 ? (
          'done'
        ) : (
          <span className={cn(s.days_left <= 14 && 'font-medium text-amber-600 dark:text-amber-400')}>
            in {s.days_left} d
          </span>
        )}
      </td>
      <td className="px-2 py-2.5 text-muted-foreground tabular-nums">
        {s.lectures_ready}
        {s.lectures !== s.lectures_ready && `/${s.lectures}`}
      </td>
      <td className="px-2 py-2.5">
        {total ? (
          <div
            className="w-28"
            title={`${s.new} new · ${s.young} learning/young · ${s.mature} mature (21+ days)`}
          >
            <div className="flex h-1.5 overflow-hidden rounded-full bg-muted">
              <div className="bg-emerald-500" style={{ width: seg(s.mature) }} />
              <div className="bg-amber-400" style={{ width: seg(s.young) }} />
              <div className="bg-sky-400/60" style={{ width: seg(s.new) }} />
            </div>
            <div className="mt-0.5 text-[11px] text-muted-foreground tabular-nums">{total} cards</div>
          </div>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </td>
      <td className="px-2 py-2.5">
        {s.mastery == null ? (
          <span className="text-muted-foreground">—</span>
        ) : (
          <div className="flex items-center gap-2">
            <div className="h-1.5 w-24 overflow-hidden rounded-full bg-muted">
              <div className="h-full rounded-full bg-primary" style={{ width: pct(s.mastery) }} />
            </div>
            <span className="text-xs tabular-nums">{pct(s.mastery)}</span>
          </div>
        )}
      </td>
      <td className="px-2 py-2.5 text-right tabular-nums">
        {s.due ? (
          <Link to={`/review?subject=${s.subject_id}`} className="text-primary hover:underline">
            {s.due}
          </Link>
        ) : (
          <span className="text-muted-foreground">0</span>
        )}
      </td>
      <td className="px-4 py-2.5 text-right text-muted-foreground tabular-nums">
        {practice == null ? '—' : pct(practice)}
      </td>
    </tr>
  )
}
