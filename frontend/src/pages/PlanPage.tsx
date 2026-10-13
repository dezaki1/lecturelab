import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  BookOpen,
  CalendarDays,
  CalendarPlus,
  Check,
  CheckCircle2,
  Circle,
  ClipboardList,
  Copy,
  Download,
  FileText,
  GraduationCap,
  Layers,
  Loader2,
  NotebookPen,
  RefreshCw,
  Target,
  Trash2,
  X,
} from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useEvent } from '@/hooks/useEvents'
import { api } from '@/lib/api'
import { formatDuration } from '@/lib/dates'
import type { Deadline, DeadlineKind, Plan, PlanItem } from '@/lib/types'
import { cn } from '@/lib/utils'

const KIND_LABEL: Record<DeadlineKind, string> = {
  deadline: 'Deadline',
  assignment: 'Assignment',
  exam: 'Exam info',
  admin: 'Admin',
  other: 'Other',
}
const WEEKDAYS = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su']
const ITEM_ICON = {
  review: Layers,
  topic: BookOpen,
  practice: Target,
  mock_exam: NotebookPen,
  cheatsheet: FileText,
  exam: GraduationCap,
  deadline: ClipboardList,
} as const

function minutes(m: number) {
  const h = Math.floor(m / 60)
  return h ? `${h} h${m % 60 ? ` ${m % 60} min` : ''}` : `${m} min`
}
function localDate(iso: string) {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(y, m - 1, d)
}
function todayIso() {
  const t = new Date()
  return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}`
}
function daysFromToday(iso: string) {
  return Math.round((localDate(iso).getTime() - localDate(todayIso()).getTime()) / 86_400_000)
}
function dayLabel(iso: string) {
  const n = daysFromToday(iso)
  if (n === 0) return 'Today'
  if (n === 1) return 'Tomorrow'
  return localDate(iso).toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'short' })
}
function shortDate(iso: string) {
  return localDate(iso).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' })
}
function relative(iso: string) {
  const n = daysFromToday(iso)
  if (n === 0) return 'today'
  if (n === 1) return 'tomorrow'
  if (n === -1) return 'yesterday'
  return n > 0 ? `in ${n} days` : `${-n} days ago`
}

export function PlanPage() {
  const qc = useQueryClient()
  const [days, setDays] = useState(14)
  const { data: plan } = useQuery({ queryKey: ['plan', days], queryFn: () => api.plan(days) })
  const { data: dl } = useQuery({ queryKey: ['deadlines'], queryFn: api.deadlines })
  useEvent('planner', () => qc.invalidateQueries({ queryKey: ['plan'] }))
  const rebuild = useMutation({
    mutationFn: api.rebuildPlan,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['plan'] }),
    onError: (e: Error) => toast.error(e.message),
  })
  const feed = `${window.location.origin}${api.calendarUrl()}`

  return (
    <div className="mx-auto max-w-6xl px-8 py-10">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="mr-auto text-2xl font-semibold tracking-tight">Plan</h1>
        <Button variant="outline" size="sm" onClick={() => rebuild.mutate()} disabled={rebuild.isPending}>
          {rebuild.isPending ? <Loader2 className="animate-spin" /> : <RefreshCw />} Rebuild plan
        </Button>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm">
              <CalendarPlus /> Add to calendar
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-72">
            <DropdownMenuItem asChild>
              <a href={api.calendarUrl(true)} download>
                <Download /> Download .ics (import once)
              </a>
            </DropdownMenuItem>
            <DropdownMenuItem
              onSelect={() =>
                navigator.clipboard
                  .writeText(feed)
                  .then(() => toast.success('Link copied', { description: feed }))
                  .catch(() => toast.error('Could not copy the link'))
              }
            >
              <Copy /> Copy subscription link (Outlook, Thunderbird)
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      <p className="mt-1 text-sm text-muted-foreground">
        Exams, deadlines and a day-by-day revision plan built from your exam dates and weak topics. It updates
        every day and when exam dates or deadlines change.
      </p>

      {dl && <ExamStrip exams={dl.exams} />}

      <div className="mt-6 grid items-start gap-6 lg:grid-cols-[3fr_2fr]">
        <section>
          {plan && <PlanDays plan={plan} />}
          {plan?.last_day && days < 120 && daysFromToday(plan.last_day) >= days && (
            <Button variant="ghost" size="sm" className="mt-2" onClick={() => setDays(120)}>
              Show the whole plan (until {shortDate(plan.last_day)})
            </Button>
          )}
          {plan && <PlanSettings plan={plan} />}
        </section>
        <section>{dl && <Deadlines dl={dl} />}</section>
      </div>
    </div>
  )
}

function ExamStrip({
  exams,
}: {
  exams: {
    subject_id: number
    subject_name: string
    subject_color: string
    date: string
    days_left: number
  }[]
}) {
  if (!exams.length)
    return (
      <p className="mt-4 rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
        No exam dates yet. Add them on the subject pages (or accept an exam announcement below) to get a
        revision plan; until then the plan only schedules your card reviews.
      </p>
    )
  return (
    <div className="mt-4 flex flex-wrap gap-2">
      {exams.map((e) => (
        <Link
          key={e.subject_id}
          to={`/subjects/${e.subject_id}`}
          className="flex items-center gap-2 rounded-lg border bg-card px-3 py-2 text-sm hover:bg-accent"
        >
          <span className="size-2 rounded-full" style={{ background: e.subject_color }} />
          <span className="font-medium">{e.subject_name}</span>
          <span className="text-muted-foreground">
            {shortDate(e.date)} · {e.days_left === 0 ? 'today' : `${e.days_left} d`}
          </span>
        </Link>
      ))}
    </div>
  )
}

function PlanDays({ plan }: { plan: Plan }) {
  if (!plan.days.length)
    return (
      <p className="text-sm text-muted-foreground">Nothing planned. Add exam dates or flashcards first.</p>
    )
  return (
    <ol className="space-y-3">
      {plan.days.map((d) => (
        <li key={d.date} className={cn('rounded-lg border bg-card p-4', !d.study_day && 'bg-card/40')}>
          <div className="flex items-baseline gap-2">
            <h2 className={cn('text-sm font-medium', daysFromToday(d.date) === 0 && 'text-primary')}>
              {dayLabel(d.date)}
            </h2>
            {d.minutes > 0 && (
              <span className="ml-auto text-xs text-muted-foreground tabular-nums">
                {d.done_minutes > 0 && `${d.done_minutes} of `}
                {minutes(d.minutes)}
              </span>
            )}
            {!d.study_day && <span className="ml-auto text-xs text-muted-foreground">day off</span>}
          </div>
          <ul className="mt-2 space-y-1">
            {d.items.map((i) => (
              <PlanRow key={i.id} item={i} />
            ))}
          </ul>
        </li>
      ))}
    </ol>
  )
}

function itemLink(i: PlanItem): string | null {
  switch (i.kind) {
    case 'review':
      return '/review'
    case 'topic':
      return i.meta.lecture_id ? `/lectures/${i.meta.lecture_id}?t=${Math.floor(i.meta.start_s ?? 0)}` : null
    case 'practice':
      return `/subjects/${i.subject_id}?tab=weak`
    case 'mock_exam':
      return `/exams?subject=${i.subject_id}`
    case 'cheatsheet':
      return `/subjects/${i.subject_id}?tab=cheatsheet`
    default:
      return null
  }
}

export function PlanRow({ item: i }: { item: PlanItem }) {
  const qc = useQueryClient()
  const toggle = useMutation({
    mutationFn: () => api.setPlanItemDone(i.id, !i.done),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['plan'] }),
  })
  const Icon = ITEM_ICON[i.kind]
  const marker = i.kind === 'exam' || i.kind === 'deadline'
  const to = itemLink(i)
  return (
    <li className={cn('flex items-center gap-2 text-sm', i.done && 'text-muted-foreground line-through')}>
      {marker ? (
        <Icon className={cn('size-4 shrink-0', i.kind === 'exam' ? 'text-red-500' : 'text-amber-500')} />
      ) : (
        <button
          type="button"
          aria-label={i.done ? 'Mark as not done' : 'Mark as done'}
          className="shrink-0 text-muted-foreground hover:text-foreground"
          onClick={() => toggle.mutate()}
        >
          {i.done ? <CheckCircle2 className="size-4 text-emerald-500" /> : <Circle className="size-4" />}
        </button>
      )}
      <span
        className="size-2 shrink-0 rounded-full bg-muted-foreground/40"
        style={i.subject_color ? { background: i.subject_color } : undefined}
      />
      {!marker && <Icon className="size-3.5 shrink-0 text-muted-foreground" />}
      <span className={cn('min-w-0 flex-1 truncate', marker && 'font-medium')} title={i.title}>
        {to ? (
          <Link to={to} className="hover:underline">
            {i.title}
          </Link>
        ) : (
          i.title
        )}
        {i.kind === 'deadline' && i.meta.due_time && (
          <span className="ml-1 text-xs text-muted-foreground">{i.meta.due_time}</span>
        )}
      </span>
      {i.kind === 'topic' && i.meta.strength != null && (
        <span className="text-xs text-muted-foreground tabular-nums" title="Topic strength">
          {Math.round(i.meta.strength * 100)}%
        </span>
      )}
      {i.minutes > 0 && (
        <span className="w-14 text-right text-xs text-muted-foreground tabular-nums">{i.minutes} min</span>
      )}
    </li>
  )
}

function PlanSettings({ plan }: { plan: Plan }) {
  const qc = useQueryClient()
  const s = plan.settings
  const [start, setStart] = useState(s.start_time)
  const save = useMutation({
    mutationFn: api.putPlannerSettings,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['plan'] }),
    onError: (e: Error) => toast.error(e.message),
  })
  return (
    <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-3 rounded-lg border p-4 text-sm">
      <div className="flex items-center gap-2">
        <Label htmlFor="plan-minutes">Time per day</Label>
        <Select
          value={String(s.minutes_per_day)}
          onValueChange={(v) => save.mutate({ minutes_per_day: Number(v) })}
        >
          <SelectTrigger id="plan-minutes" size="sm" className="w-28">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {[30, 45, 60, 90, 120, 150, 180, 240, 300].map((m) => (
              <SelectItem key={m} value={String(m)}>
                {minutes(m)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="flex items-center gap-1" role="group" aria-label="Study days">
        {WEEKDAYS.map((w, i) => {
          const on = s.weekdays.includes(i)
          return (
            <Button
              key={w}
              size="xs"
              variant={on ? 'secondary' : 'ghost'}
              aria-pressed={on}
              className={cn('w-8', !on && 'text-muted-foreground')}
              onClick={() =>
                save.mutate({ weekdays: on ? s.weekdays.filter((x) => x !== i) : [...s.weekdays, i] })
              }
            >
              {w}
            </Button>
          )
        })}
      </div>
      <div className="flex items-center gap-2">
        <Label htmlFor="plan-start">In the calendar at</Label>
        <Input
          id="plan-start"
          type="time"
          className="h-8 w-28"
          value={start}
          onChange={(e) => setStart(e.target.value)}
          onBlur={() => start && start !== s.start_time && save.mutate({ start_time: start })}
        />
      </div>
    </div>
  )
}

function Deadlines({ dl }: { dl: { suggested: Deadline[]; open: Deadline[]; done: Deadline[] } }) {
  const qc = useQueryClient()
  useEvent('deadlines', () => qc.invalidateQueries({ queryKey: ['deadlines'] }))
  const [showDone, setShowDone] = useState(false)
  return (
    <div className="space-y-4">
      {dl.suggested.length > 0 && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-4">
          <h2 className="text-sm font-medium">From your lectures: to review ({dl.suggested.length})</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">
            Found in the announcements. Accept what matters; only accepted items go into the plan and
            calendar.
          </p>
          <ul className="mt-3 space-y-3">
            {dl.suggested.map((d) => (
              <Suggestion key={d.id} d={d} />
            ))}
          </ul>
        </div>
      )}

      <div className="rounded-lg border bg-card p-4">
        <h2 className="flex items-center gap-2 text-sm font-medium">
          <CalendarDays className="size-4" /> Deadlines
        </h2>
        {dl.open.length ? (
          <ul className="mt-2 divide-y">
            {dl.open.map((d) => (
              <OpenRow key={d.id} d={d} />
            ))}
          </ul>
        ) : (
          <p className="mt-2 text-sm text-muted-foreground">No open deadlines.</p>
        )}
        <AddDeadline />
        {dl.done.length > 0 && (
          <div className="mt-3 border-t pt-2">
            <Button variant="ghost" size="xs" onClick={() => setShowDone(!showDone)}>
              {showDone ? 'Hide' : 'Show'} done ({dl.done.length})
            </Button>
            {showDone && (
              <ul className="mt-1 divide-y">
                {dl.done.map((d) => (
                  <OpenRow key={d.id} d={d} />
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

function useDeadlineMutations() {
  const qc = useQueryClient()
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['deadlines'] })
    qc.invalidateQueries({ queryKey: ['plan'] })
  }
  const onError = (e: Error) => toast.error(e.message)
  return {
    update: useMutation({
      mutationFn: (v: { id: number } & Parameters<typeof api.updateDeadline>[1]) =>
        api.updateDeadline(v.id, v),
      onSuccess: refresh,
      onError,
    }),
    remove: useMutation({ mutationFn: api.deleteDeadline, onSuccess: refresh, onError }),
    examDate: useMutation({
      mutationFn: (v: { subject_id: number; date: string }) =>
        api.updateSubject(v.subject_id, { exam_date: v.date }),
      onSuccess: () => {
        refresh()
        qc.invalidateQueries({ queryKey: ['subjects'] })
        qc.invalidateQueries({ queryKey: ['dashboard'] })
        toast.success('Exam date saved')
      },
      onError,
    }),
  }
}

function Source({ d }: { d: Deadline }) {
  return (
    <div className="truncate text-xs text-muted-foreground">
      {d.subject_name}
      {d.lecture_id && (
        <>
          {' · '}
          <Link className="hover:underline" to={`/lectures/${d.lecture_id}?t=${Math.floor(d.t_s ?? 0)}`}>
            {d.lecture_title}
            {d.t_s != null && ` at ${formatDuration(d.t_s)}`}
          </Link>
        </>
      )}
    </div>
  )
}

function Suggestion({ d }: { d: Deadline }) {
  const { update, remove, examDate } = useDeadlineMutations()
  const [date, setDate] = useState(d.due_date ?? '')
  const canSetExam = d.kind === 'exam' && date && d.subject_id && d.subject_exam_date !== date
  return (
    <li className="space-y-1.5">
      <div className="flex items-start gap-2">
        <Badge variant="outline" className="shrink-0">
          {KIND_LABEL[d.kind]}
        </Badge>
        <div className="min-w-0 flex-1">
          <div className="text-sm">{d.title}</div>
          <Source d={d} />
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <Input
          type="date"
          aria-label="Due date"
          className="h-7 w-36 text-xs"
          value={date}
          onChange={(e) => setDate(e.target.value)}
        />
        <Button size="xs" onClick={() => update.mutate({ id: d.id, status: 'open', due_date: date || null })}>
          <Check /> Accept
        </Button>
        {canSetExam && (
          <Button
            size="xs"
            variant="outline"
            title={d.subject_exam_date ? `Currently ${shortDate(d.subject_exam_date)}` : undefined}
            onClick={() => {
              examDate.mutate({ subject_id: d.subject_id!, date })
              update.mutate({ id: d.id, status: 'open', due_date: date })
            }}
          >
            <GraduationCap /> Set as exam date
          </Button>
        )}
        <Button size="xs" variant="ghost" className="ml-auto" onClick={() => remove.mutate(d.id)}>
          <X /> Dismiss
        </Button>
      </div>
    </li>
  )
}

function OpenRow({ d }: { d: Deadline }) {
  const { update, remove } = useDeadlineMutations()
  const done = d.status === 'done'
  const overdue = !done && d.due_date && daysFromToday(d.due_date) < 0
  return (
    <li className="flex items-center gap-2 py-2 text-sm">
      <button
        type="button"
        aria-label={done ? 'Mark as open' : 'Mark as done'}
        className="shrink-0 text-muted-foreground hover:text-foreground"
        onClick={() => update.mutate({ id: d.id, status: done ? 'open' : 'done' })}
      >
        {done ? <CheckCircle2 className="size-4 text-emerald-500" /> : <Circle className="size-4" />}
      </button>
      <span
        className="size-2 shrink-0 rounded-full bg-muted-foreground/40"
        style={d.subject_color ? { background: d.subject_color } : undefined}
      />
      <div className={cn('min-w-0 flex-1', done && 'text-muted-foreground line-through')}>
        <div className="truncate" title={d.title}>
          {d.title}
        </div>
        <Source d={d} />
      </div>
      <div
        className={cn('shrink-0 text-right text-xs', overdue ? 'text-destructive' : 'text-muted-foreground')}
      >
        {d.due_date ? (
          <>
            <div>
              {shortDate(d.due_date)}
              {d.due_time && ` ${d.due_time}`}
            </div>
            {!done && <div>{relative(d.due_date)}</div>}
          </>
        ) : (
          'no date'
        )}
      </div>
      <Button size="icon-xs" variant="ghost" aria-label="Delete" onClick={() => remove.mutate(d.id)}>
        <Trash2 />
      </Button>
    </li>
  )
}

function AddDeadline() {
  const qc = useQueryClient()
  const { data: subjects = [] } = useQuery({ queryKey: ['subjects'], queryFn: api.subjects })
  const [title, setTitle] = useState('')
  const [date, setDate] = useState('')
  const [time, setTime] = useState('')
  const [subject, setSubject] = useState('0')
  const [kind, setKind] = useState<DeadlineKind>('deadline')
  const add = useMutation({
    mutationFn: () =>
      api.addDeadline({
        title: title.trim(),
        kind,
        due_date: date || null,
        due_time: time,
        subject_id: Number(subject) || null,
      }),
    onSuccess: () => {
      setTitle('')
      setDate('')
      setTime('')
      qc.invalidateQueries({ queryKey: ['deadlines'] })
      qc.invalidateQueries({ queryKey: ['plan'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })
  return (
    <form
      className="mt-3 space-y-2 border-t pt-3"
      onSubmit={(e: FormEvent) => {
        e.preventDefault()
        if (title.trim()) add.mutate()
      }}
    >
      <Input
        placeholder="Add a deadline, e.g. Exercise sheet 4"
        aria-label="New deadline"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        className="h-8"
      />
      {title.trim() && (
        <div className="flex flex-wrap items-center gap-1.5">
          <Input
            type="date"
            aria-label="Due date"
            className="h-8 w-36"
            value={date}
            onChange={(e) => setDate(e.target.value)}
          />
          <Input
            type="time"
            aria-label="Due time"
            className="h-8 w-28"
            value={time}
            onChange={(e) => setTime(e.target.value)}
          />
          <Select value={subject} onValueChange={setSubject}>
            <SelectTrigger size="sm" className="w-40" aria-label="Subject">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="0">No subject</SelectItem>
              {subjects.map((s) => (
                <SelectItem key={s.id} value={String(s.id)}>
                  {s.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={kind} onValueChange={(v) => setKind(v as DeadlineKind)}>
            <SelectTrigger size="sm" className="w-32" aria-label="Kind">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {(Object.keys(KIND_LABEL) as DeadlineKind[]).map((k) => (
                <SelectItem key={k} value={k}>
                  {KIND_LABEL[k]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button size="sm" type="submit" disabled={add.isPending}>
            Add
          </Button>
        </div>
      )}
    </form>
  )
}
