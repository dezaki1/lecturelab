import { useQuery } from '@tanstack/react-query'
import { CalendarClock, Flame, Layers, Target } from 'lucide-react'
import { Link } from 'react-router'

import { WeakSpotsCard } from '@/components/hub/WeakSpots'
import { MoodleReview } from '@/components/MoodleWatch'
import { CourseReadiness } from '@/components/CourseReadiness'
import { PlanTodayCard } from '@/components/PlanTodayCard'
import { TodayCard } from '@/components/TodayCard'
import { Button } from '@/components/ui/button'
import { useEvent } from '@/hooks/useEvents'
import { api } from '@/lib/api'
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

      <CourseReadiness subjects={d.subjects} />

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
