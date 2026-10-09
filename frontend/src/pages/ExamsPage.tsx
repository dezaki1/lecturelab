import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  Check,
  ChevronDown,
  Clock,
  Flag,
  Loader2,
  RotateCcw,
  Star,
  Timer,
  Trash2,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { toast } from 'sonner'

import { Markdown } from '@/components/Markdown'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { api } from '@/lib/api'
import { formatDateTime } from '@/lib/dates'
import type { ExamDetail, ExamItem, ExamSummary } from '@/lib/types'
import { cn } from '@/lib/utils'

const TYPES: [string, string][] = [
  ['mcq', 'Multiple choice'],
  ['multi', 'Multiple select'],
  ['tf', 'True/false'],
  ['short', 'Short answer'],
  ['long', 'Long answer'],
  ['calculation', 'Calculation'],
  ['code', 'Code'],
]
const TYPE_LABEL = Object.fromEntries(TYPES)
const CHOICE = new Set(['mcq', 'multi', 'tf'])
const DURATIONS = [15, 30, 45, 60, 90, 120, 150, 180]
const DIFFICULTY: Record<string, string> = {
  exam: 'Like the real exam',
  easy: 'Warm-up (easier)',
  medium: 'Medium',
  hard: 'Harder than the exam',
}

const pct = (score: number | null, max: number) =>
  score == null || !max ? null : Math.round((score / max) * 100)

/** The usual Austrian 5-step scale (50 / 62.5 / 75 / 87.5 %); courses may differ. */
function grade(percent: number): string {
  if (percent >= 87.5) return '1 (Sehr gut)'
  if (percent >= 75) return '2 (Gut)'
  if (percent >= 62.5) return '3 (Befriedigend)'
  if (percent >= 50) return '4 (Genügend)'
  return '5 (Nicht genügend)'
}

function clock(seconds: number): string {
  const s = Math.max(0, Math.ceil(seconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  const mm = String(m).padStart(h ? 2 : 1, '0')
  return `${h ? `${h}:` : ''}${mm}:${String(sec).padStart(2, '0')}`
}

// Overview: new exam + history ---------------------------------------------------------------------
export function ExamsPage() {
  const { data: exams = [] } = useQuery({ queryKey: ['exams'], queryFn: () => api.exams() })
  return (
    <div className="mx-auto max-w-4xl px-8 py-10">
      <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
        <Timer className="size-5 text-primary" /> Exam simulator
      </h1>
      <p className="mt-1 text-sm text-muted-foreground">
        Sit a timed mock exam on the topics you pick. The AI grades open answers against a rubric with partial
        credit and links every question to the lecture moment it comes from.
      </p>
      <NewExamForm />
      <h2 className="mt-10 mb-3 text-sm font-medium text-muted-foreground">History</h2>
      {exams.length === 0 ? (
        <p className="text-sm text-muted-foreground">No exams yet.</p>
      ) : (
        <ul className="divide-y rounded-lg border bg-card text-sm">
          {exams.map((e) => (
            <HistoryRow key={e.id} exam={e} />
          ))}
        </ul>
      )}
    </div>
  )
}

function HistoryRow({ exam: e }: { exam: ExamSummary }) {
  const p = pct(e.score, e.max_points)
  const status: Record<string, string> = {
    building: 'being written…',
    ready: 'not started',
    running: 'in progress',
    grading: 'grading…',
    failed: 'failed',
  }
  return (
    <li>
      <Link to={`/exams/${e.id}`} className="flex items-center gap-3 px-4 py-2.5 hover:bg-muted/50">
        <span className="size-2 shrink-0 rounded-full" style={{ background: e.subject_color }} />
        <span className="min-w-0 flex-1 truncate">{e.title}</span>
        <span className="shrink-0 text-xs text-muted-foreground">
          {formatDateTime(e.created_at)} · {e.duration_min} min
        </span>
        <span
          className={cn(
            'w-28 shrink-0 text-right tabular-nums',
            e.status === 'failed' && 'text-destructive',
            p != null && (p >= 50 ? 'text-emerald-600' : 'text-destructive'),
          )}
        >
          {e.status === 'graded' ? `${e.score}/${e.max_points} · ${p}%` : status[e.status]}
        </span>
      </Link>
    </li>
  )
}

function Chip({
  on,
  onClick,
  children,
  title,
}: {
  on: boolean
  onClick: () => void
  children: React.ReactNode
  title?: string
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      className={cn(
        'inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs transition',
        on ? 'border-primary bg-primary/10 text-foreground' : 'text-muted-foreground hover:bg-muted/60',
      )}
    >
      {children}
    </button>
  )
}

function NewExamForm() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { data: subjects = [] } = useQuery({ queryKey: ['subjects'], queryFn: api.subjects })
  const [subject, setSubject] = useState<string>('')
  const subjectId = Number(subject || subjects[0]?.id || 0)
  const { data: opts } = useQuery({
    queryKey: ['exam-options', subjectId],
    queryFn: () => api.examOptions(subjectId),
    enabled: subjectId > 0,
  })
  const [duration, setDuration] = useState<number | null>(null)
  const [topics, setTopics] = useState<string[]>([])
  const [types, setTypes] = useState<string[]>([])
  const [difficulty, setDifficulty] = useState('exam')
  const [source, setSource] = useState<'generate' | 'bank'>('generate')
  const [title, setTitle] = useState('')
  const minutes = duration ?? opts?.suggested_duration ?? 90

  const create = useMutation({
    mutationFn: () =>
      api.createExam({
        subject_id: subjectId,
        duration_min: minutes,
        topics,
        types,
        difficulty,
        source,
        title,
      }),
    onSuccess: (e) => {
      qc.invalidateQueries({ queryKey: ['exams'] })
      navigate(`/exams/${e.id}`)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const changeSubject = (v: string) => {
    setSubject(v)
    setTopics([])
    setDuration(null)
  }
  const toggle = (list: string[], set: (x: string[]) => void, v: string) =>
    set(list.includes(v) ? list.filter((x) => x !== v) : [...list, v])

  if (subjects.length === 0)
    return <p className="mt-6 text-sm text-muted-foreground">Create a subject and add lectures first.</p>

  return (
    <div className="mt-6 grid gap-5 rounded-lg border bg-card p-5">
      <div className="grid gap-3 sm:grid-cols-[1fr_9rem_12rem]">
        <Field label="Subject">
          <Select value={String(subjectId)} onValueChange={changeSubject}>
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {subjects.map((s) => (
                <SelectItem key={s.id} value={String(s.id)}>
                  {s.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <Field label="Duration">
          <Select value={String(minutes)} onValueChange={(v) => setDuration(Number(v))}>
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {[...new Set([...DURATIONS, minutes])]
                .sort((a, b) => a - b)
                .map((d) => (
                  <SelectItem key={d} value={String(d)}>
                    {d} min
                  </SelectItem>
                ))}
            </SelectContent>
          </Select>
        </Field>
        <Field label="Difficulty">
          <Select value={difficulty} onValueChange={setDifficulty}>
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {Object.entries(DIFFICULTY).map(([k, v]) => (
                <SelectItem key={k} value={k}>
                  {v}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
      </div>

      <Field label={`Topics ${topics.length ? `(${topics.length})` : '(none picked = the whole course)'}`}>
        {opts && opts.topics.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No topic list yet: it comes with the course overview (subject page). The exam covers all lectures.
          </p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {opts?.topics.map((t) => (
              <Chip
                key={t.name}
                on={topics.includes(t.name)}
                onClick={() => toggle(topics, setTopics, t.name)}
                title={t.mastery != null ? `Mastery ${Math.round(t.mastery * 100)}%` : 'No reviews yet'}
              >
                {t.exam_relevant && <Star className="size-3 fill-amber-400 text-amber-500" />}
                {t.name}
                {t.mastery != null && (
                  <span className="text-muted-foreground">· {Math.round(t.mastery * 100)}%</span>
                )}
              </Chip>
            ))}
          </div>
        )}
      </Field>

      <Field
        label={`Question types ${types.length ? '' : opts?.past_exams.length ? '(like the past exams)' : '(mixed)'}`}
      >
        <div className="flex flex-wrap gap-1.5">
          {TYPES.map(([k, label]) => (
            <Chip key={k} on={types.includes(k)} onClick={() => toggle(types, setTypes, k)}>
              {label}
              {source === 'bank' && <span className="text-muted-foreground">· {opts?.bank[k] ?? 0}</span>}
            </Chip>
          ))}
        </div>
      </Field>

      <div className="grid gap-3 sm:grid-cols-2">
        <SourceOption
          on={source === 'generate'}
          onClick={() => setSource('generate')}
          title="Write a new exam"
          text="The AI writes fresh questions from your notes of these topics (about a minute)."
        />
        <SourceOption
          on={source === 'bank'}
          onClick={() => setSource('bank')}
          title="Use my question bank"
          text={`Instant and offline: questions generated with your lectures, unseen and weakest first (${Object.values(
            opts?.bank ?? {},
          ).reduce((a, b) => a + b, 0)} available).`}
        />
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <Field label="Title (optional)" className="min-w-56 flex-1">
          <Input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="e.g. Midterm rehearsal"
          />
        </Field>
        <Button onClick={() => create.mutate()} disabled={!subjectId || create.isPending}>
          {create.isPending ? <Loader2 className="animate-spin" /> : <Timer />} Create exam
        </Button>
      </div>
      {opts && (
        <p className="-mt-2 text-xs text-muted-foreground">
          About {Math.max(1, Math.round(minutes * opts.points_per_min))} points
          {opts.past_exams.length > 0
            ? `, sized and styled like your ${opts.past_exams.length} past exam${opts.past_exams.length === 1 ? '' : 's'}.`
            : '. Add past exams on the subject’s Materials tab to match the real format.'}
          {source === 'generate' && !opts.llm_configured && ' Writing an exam needs an API key (Settings).'}
        </p>
      )}
    </div>
  )
}

function SourceOption(p: { on: boolean; onClick: () => void; title: string; text: string }) {
  return (
    <button
      type="button"
      onClick={p.onClick}
      className={cn(
        'rounded-lg border px-4 py-3 text-left transition',
        p.on ? 'border-primary bg-primary/5' : 'hover:bg-muted/50',
      )}
    >
      <div className="text-sm font-medium">{p.title}</div>
      <div className="mt-0.5 text-xs text-muted-foreground">{p.text}</div>
    </button>
  )
}

function Field({
  label,
  children,
  className,
}: {
  label: string
  children: React.ReactNode
  className?: string
}) {
  return (
    <div className={cn('grid gap-1.5', className)}>
      <Label>{label}</Label>
      {children}
    </div>
  )
}

// One exam ---------------------------------------------------------------------------------------
export function ExamPage() {
  const id = Number(useParams().id)
  const { data: exam, error } = useQuery({
    queryKey: ['exam', id],
    queryFn: () => api.exam(id),
    // Events update it; polling is the fallback while a job works on it.
    refetchInterval: (q) => (['building', 'grading'].includes(q.state.data?.status ?? '') ? 5000 : false),
  })
  if (error) return <div className="p-10 text-sm text-destructive">{error.message}</div>
  if (!exam) return null
  if (exam.status === 'running') return <Sitting key={exam.id} exam={exam} />
  return (
    <div className="mx-auto max-w-3xl px-8 py-10">
      <Link to="/exams" className="text-sm text-muted-foreground hover:text-foreground">
        ← Exam simulator
      </Link>
      <h1 className="mt-2 text-2xl font-semibold tracking-tight">{exam.title}</h1>
      <div className="mt-1 flex flex-wrap gap-x-3 text-sm text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <span className="size-2 rounded-full" style={{ background: exam.subject_color }} />
          {exam.subject_name}
        </span>
        <span>{exam.duration_min} min</span>
        {exam.item_count > 0 && (
          <span>
            {exam.item_count} questions · {exam.max_points} points
          </span>
        )}
        <span>{DIFFICULTY[exam.difficulty] ?? exam.difficulty}</span>
      </div>
      {exam.status === 'building' && (
        <Working text="Writing your exam…" sub="This usually takes a minute or two." />
      )}
      {exam.status === 'grading' && (
        <Working text="Grading your answers…" sub="Open answers are checked against the rubric." />
      )}
      {exam.status === 'failed' && <Failed exam={exam} />}
      {exam.status === 'ready' && <Ready exam={exam} />}
      {exam.status === 'graded' && <Results exam={exam} />}
    </div>
  )
}

function Working({ text, sub }: { text: string; sub: string }) {
  return (
    <div className="mt-10 flex flex-col items-center gap-2 rounded-lg border bg-card py-12 text-center">
      <Loader2 className="size-6 animate-spin text-primary" />
      <div className="font-medium">{text}</div>
      <div className="text-sm text-muted-foreground">{sub}</div>
    </div>
  )
}

function DeleteExam({ exam }: { exam: ExamDetail }) {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const del = useMutation({
    mutationFn: () => api.deleteExam(exam.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['exams'] })
      navigate('/exams')
    },
    onError: (e: Error) => toast.error(e.message),
  })
  return (
    <Button variant="ghost" onClick={() => del.mutate()} disabled={del.isPending}>
      <Trash2 /> Delete
    </Button>
  )
}

function Failed({ exam }: { exam: ExamDetail }) {
  const qc = useQueryClient()
  const retry = useMutation({
    mutationFn: () => api.retryExam(exam.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['exam', exam.id] }),
    onError: (e: Error) => toast.error(e.message),
  })
  return (
    <div className="mt-8 rounded-lg border border-destructive/40 bg-destructive/5 p-5 text-sm">
      <div className="flex items-center gap-2 font-medium text-destructive">
        <AlertTriangle className="size-4" />
        {exam.submitted_at ? 'Grading failed' : 'Writing the exam failed'}
      </div>
      <p className="mt-1 text-muted-foreground">{exam.error}</p>
      {exam.submitted_at && <p className="mt-1 text-muted-foreground">Your answers are saved.</p>}
      <div className="mt-4 flex gap-2">
        <Button onClick={() => retry.mutate()} disabled={retry.isPending}>
          <RotateCcw /> Try again
        </Button>
        <DeleteExam exam={exam} />
      </div>
    </div>
  )
}

function Ready({ exam }: { exam: ExamDetail }) {
  const qc = useQueryClient()
  const start = useMutation({
    mutationFn: () => api.startExam(exam.id),
    onSuccess: (e) => qc.setQueryData(['exam', exam.id], e),
    onError: (e: Error) => toast.error(e.message),
  })
  const types = [...new Set(exam.items.map((i) => TYPE_LABEL[i.question.type] ?? i.question.type))]
  return (
    <div className="mt-8 rounded-lg border bg-card p-6">
      <ul className="space-y-1.5 text-sm">
        <li>
          <span className="text-muted-foreground">Questions: </span>
          {types.join(', ')}
        </li>
        {exam.topics.length > 0 && (
          <li>
            <span className="text-muted-foreground">Topics: </span>
            {exam.topics.join(', ')}
          </li>
        )}
        <li className="text-muted-foreground">
          The clock starts when you press Start and keeps running if you close the tab. Answers are saved as
          you type. When the time is up, the exam is handed in automatically.
        </li>
      </ul>
      <div className="mt-6 flex gap-2">
        <Button onClick={() => start.mutate()} disabled={start.isPending}>
          <Clock /> Start ({exam.duration_min} min)
        </Button>
        <DeleteExam exam={exam} />
      </div>
    </div>
  )
}

// Sitting the exam -------------------------------------------------------------------------------
type Draft = { answer_text: string; selected: number[]; flagged: boolean }

function Sitting({ exam }: { exam: ExamDetail }) {
  const qc = useQueryClient()
  const [drafts, setDrafts] = useState<Record<number, Draft>>(() =>
    Object.fromEntries(
      exam.items.map((i) => [i.id, { answer_text: i.answer_text, selected: i.selected, flagged: i.flagged }]),
    ),
  )
  const [saving, setSaving] = useState(0)
  const [confirm, setConfirm] = useState(false)
  const timers = useRef<Record<number, number>>({})
  const pending = useRef<Record<number, Promise<unknown>>>({})
  const submitted = useRef(false)
  // Deadline in local clock time, from the server's remaining seconds (no clock-skew issues).
  const deadline = useRef(Date.now() + (exam.remaining_s ?? 0) * 1000)
  const [left, setLeft] = useState(exam.remaining_s ?? 0)

  const save = useCallback(
    (itemId: number, data: Partial<Draft>) => {
      setSaving((n) => n + 1)
      const p = api
        .saveExamAnswer(exam.id, itemId, data)
        .then((r) => {
          if (r.remaining_s != null) deadline.current = Date.now() + r.remaining_s * 1000
        })
        .catch((e: Error) => toast.error(`Not saved: ${e.message}`))
        .finally(() => setSaving((n) => n - 1))
      pending.current[itemId] = p
      return p
    },
    [exam.id],
  )

  const update = (item: ExamItem, data: Partial<Draft>, debounce = false) => {
    setDrafts((d) => ({ ...d, [item.id]: { ...d[item.id], ...data } }))
    window.clearTimeout(timers.current[item.id])
    if (debounce) timers.current[item.id] = window.setTimeout(() => save(item.id, data), 800)
    else save(item.id, data)
  }

  const submit = useMutation({
    mutationFn: async () => {
      submitted.current = true
      // Flush typed answers that are still waiting for their debounce.
      for (const [itemId, t] of Object.entries(timers.current)) {
        window.clearTimeout(t)
        const d = draftsRef.current[Number(itemId)]
        if (d) await save(Number(itemId), { answer_text: d.answer_text })
      }
      timers.current = {}
      await Promise.all(Object.values(pending.current))
      return api.submitExam(exam.id)
    },
    onSuccess: (e) => {
      qc.setQueryData(['exam', exam.id], e)
      qc.invalidateQueries({ queryKey: ['exams'] })
    },
    onError: (e: Error) => {
      submitted.current = false
      toast.error(e.message)
      qc.invalidateQueries({ queryKey: ['exam', exam.id] })
    },
  })
  const draftsRef = useRef(drafts)
  draftsRef.current = drafts
  const submitRef = useRef(submit.mutate)
  submitRef.current = submit.mutate

  useEffect(() => {
    const tick = () => {
      const s = (deadline.current - Date.now()) / 1000
      setLeft(s)
      if (s <= 0 && !submitted.current) {
        toast.info('Time is up. Your exam was handed in.')
        submitRef.current()
      }
    }
    tick()
    const t = window.setInterval(tick, 1000)
    return () => window.clearInterval(t)
  }, [])

  const answered = exam.items.filter((i) => {
    const d = drafts[i.id]
    return d.selected.length > 0 || d.answer_text.trim() !== ''
  }).length
  const flagged = exam.items.filter((i) => drafts[i.id].flagged).length

  return (
    <div className="pb-24">
      <div className="sticky top-0 z-10 border-b bg-background/95 backdrop-blur">
        <div className="mx-auto flex max-w-3xl items-center gap-4 px-8 py-3">
          <div
            className={cn(
              'flex items-center gap-1.5 font-mono text-lg tabular-nums',
              left < 300 && 'text-destructive',
            )}
          >
            <Clock className="size-4" /> {clock(left)}
          </div>
          <div className="flex min-w-0 flex-1 flex-wrap gap-1">
            {exam.items.map((i) => {
              const d = drafts[i.id]
              const done = d.selected.length > 0 || d.answer_text.trim() !== ''
              return (
                <a
                  key={i.id}
                  href={`#q-${i.idx + 1}`}
                  className={cn(
                    'relative flex size-6 items-center justify-center rounded text-xs tabular-nums',
                    done ? 'bg-primary/15 text-foreground' : 'bg-muted text-muted-foreground',
                  )}
                >
                  {i.idx + 1}
                  {d.flagged && (
                    <span className="absolute -top-0.5 -right-0.5 size-1.5 rounded-full bg-amber-500" />
                  )}
                </a>
              )
            })}
          </div>
          <span className="shrink-0 text-xs text-muted-foreground">{saving > 0 ? 'Saving…' : 'Saved'}</span>
          <Button size="sm" onClick={() => setConfirm(true)} disabled={submit.isPending}>
            Hand in
          </Button>
        </div>
      </div>

      <div className="mx-auto max-w-3xl space-y-5 px-8 pt-6">
        <h1 className="text-xl font-semibold tracking-tight">{exam.title}</h1>
        {exam.items.map((item) => (
          <QuestionCard key={item.id} item={item}>
            <AnswerInput item={item} draft={drafts[item.id]} onChange={update} />
            <div className="mt-3 flex justify-end">
              <Button
                size="xs"
                variant={drafts[item.id].flagged ? 'secondary' : 'ghost'}
                onClick={() => update(item, { flagged: !drafts[item.id].flagged })}
              >
                <Flag className={cn(drafts[item.id].flagged && 'fill-amber-400 text-amber-500')} />
                {drafts[item.id].flagged ? 'Flagged' : 'Flag for later'}
              </Button>
            </div>
          </QuestionCard>
        ))}
        <div className="flex justify-end">
          <Button onClick={() => setConfirm(true)} disabled={submit.isPending}>
            Hand in
          </Button>
        </div>
      </div>

      <AlertDialog open={confirm} onOpenChange={setConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Hand in your exam?</AlertDialogTitle>
            <AlertDialogDescription>
              {answered} of {exam.items.length} questions answered
              {flagged > 0 ? `, ${flagged} flagged` : ''}. {clock(left)} left. You can&apos;t change answers
              afterwards.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep working</AlertDialogCancel>
            <AlertDialogAction onClick={() => submit.mutate()}>Hand in</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function QuestionCard({
  item,
  children,
  right,
}: {
  item: ExamItem
  children: React.ReactNode
  right?: React.ReactNode
}) {
  const q = item.question
  return (
    <article id={`q-${item.idx + 1}`} className="scroll-mt-20 rounded-xl border bg-card p-6">
      <header className="mb-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span className="text-sm font-semibold text-foreground">{item.idx + 1}.</span>
        <Badge variant="outline">{TYPE_LABEL[q.type] ?? q.type}</Badge>
        {q.topic && <span>{q.topic}</span>}
        {q.emphasized && <Star className="size-3 fill-amber-400 text-amber-500" />}
        <span className="ml-auto">{right ?? `${q.points} pt${q.points === 1 ? '' : 's'}`}</span>
      </header>
      {q.title && <h2 className="mb-2 font-semibold">{q.title}</h2>}
      <Markdown>{q.prompt}</Markdown>
      {children}
    </article>
  )
}

function AnswerInput({
  item,
  draft,
  onChange,
}: {
  item: ExamItem
  draft: Draft
  onChange: (item: ExamItem, data: Partial<Draft>, debounce?: boolean) => void
}) {
  const q = item.question
  if (CHOICE.has(q.type) && q.options.length > 0) {
    const toggle = (i: number) =>
      onChange(item, {
        selected:
          q.type === 'multi'
            ? draft.selected.includes(i)
              ? draft.selected.filter((x) => x !== i)
              : [...draft.selected, i]
            : draft.selected.includes(i)
              ? []
              : [i],
      })
    return (
      <>
        {q.type === 'multi' && <p className="mt-2 text-xs text-muted-foreground">Select all that apply.</p>}
        <ul className="mt-3 space-y-2">
          {q.options.map((o, i) => (
            <li key={i}>
              <button
                type="button"
                onClick={() => toggle(i)}
                className={cn(
                  'flex w-full items-start gap-3 rounded-lg border px-4 py-2.5 text-left text-sm transition',
                  draft.selected.includes(i) ? 'border-primary bg-primary/5' : 'hover:bg-muted/50',
                )}
              >
                <span
                  className={cn(
                    'mt-0.5 flex size-4 shrink-0 items-center justify-center border',
                    q.type === 'multi' ? 'rounded-sm' : 'rounded-full',
                    draft.selected.includes(i) && 'border-primary bg-primary text-primary-foreground',
                  )}
                >
                  {draft.selected.includes(i) && <Check className="size-3" />}
                </span>
                <Markdown className="flex-1">{o.text}</Markdown>
              </button>
            </li>
          ))}
        </ul>
      </>
    )
  }
  return (
    <Textarea
      className={cn('mt-4', q.type === 'code' ? 'min-h-48 font-mono text-sm' : 'min-h-32')}
      placeholder={q.type === 'calculation' ? 'Show your working…' : 'Your answer…'}
      value={draft.answer_text}
      spellCheck={q.type !== 'code'}
      onChange={(e) => onChange(item, { answer_text: e.target.value }, true)}
    />
  )
}

// Results ----------------------------------------------------------------------------------------
function Results({ exam }: { exam: ExamDetail }) {
  const p = pct(exam.score, exam.max_points) ?? 0
  const taken =
    exam.started_at && exam.submitted_at
      ? Math.round((new Date(exam.submitted_at).getTime() - new Date(exam.started_at).getTime()) / 60000)
      : null
  const byTopic = useMemo(() => {
    const m = new Map<string, { score: number; max: number }>()
    for (const i of exam.items) {
      const k = i.question.topic || 'Other'
      const t = m.get(k) ?? { score: 0, max: 0 }
      t.score += i.score ?? 0
      t.max += i.max_points
      m.set(k, t)
    }
    return [...m.entries()].sort((a, b) => a[1].score / a[1].max - b[1].score / b[1].max)
  }, [exam.items])

  return (
    <div className="mt-6 space-y-6">
      <section className="rounded-xl border bg-card p-6">
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <span
            className={cn(
              'text-4xl font-semibold tabular-nums',
              p >= 50 ? 'text-emerald-600' : 'text-destructive',
            )}
          >
            {p}%
          </span>
          <span className="text-lg tabular-nums">
            {exam.score} / {exam.max_points} points
          </span>
          <span className="text-sm text-muted-foreground">≈ grade {grade(p)}</span>
        </div>
        <div className="mt-1 text-xs text-muted-foreground">
          {taken != null && `${taken} of ${exam.duration_min} min used · `}
          {exam.submitted_at && `handed in ${formatDateTime(exam.submitted_at)}`}
          {exam.cost_usd > 0 && ` · $${exam.cost_usd.toFixed(3)}`}
        </div>
        {byTopic.length > 1 && (
          <div className="mt-5 grid gap-1.5 text-sm">
            {byTopic.map(([name, t]) => {
              const tp = t.max ? (t.score / t.max) * 100 : 0
              return (
                <div key={name} className="grid grid-cols-[minmax(0,1fr)_8rem_4.5rem] items-center gap-3">
                  <span className="truncate">{name}</span>
                  <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                    <div
                      className={cn('h-full', tp >= 50 ? 'bg-emerald-500' : 'bg-destructive')}
                      style={{ width: `${tp}%` }}
                    />
                  </div>
                  <span className="text-right text-xs tabular-nums text-muted-foreground">
                    {Math.round(t.score * 10) / 10}/{t.max}
                  </span>
                </div>
              )
            })}
          </div>
        )}
        {exam.feedback_md && (
          <div className="mt-5 border-t pt-4 text-sm">
            <Markdown>{exam.feedback_md}</Markdown>
          </div>
        )}
      </section>

      {exam.items.map((item) => (
        <ReviewedQuestion key={item.id} item={item} />
      ))}
      <div className="flex gap-2">
        <Button asChild variant="outline">
          <Link to="/exams">
            <Timer /> New exam
          </Link>
        </Button>
        <DeleteExam exam={exam} />
      </div>
    </div>
  )
}

function ScoreMark({ score, max }: { score: number; max: number }) {
  if (score >= max - 1e-9) return <Check className="size-4 text-emerald-500" />
  if (score > 0) return <span className="w-4 text-center font-semibold text-amber-500">~</span>
  return <X className="size-4 text-destructive" />
}

function ReviewedQuestion({ item }: { item: ExamItem }) {
  const q = item.question
  const [showAnswer, setShowAnswer] = useState(false)
  const score = item.score ?? 0
  const choice = CHOICE.has(q.type) && q.options.length > 0
  return (
    <QuestionCard
      item={item}
      right={
        <span className="flex items-center gap-1.5 text-sm font-medium tabular-nums text-foreground">
          <ScoreMark score={score} max={item.max_points} /> {score}/{item.max_points}
        </span>
      }
    >
      {choice ? (
        <ul className="mt-3 space-y-1.5 text-sm">
          {q.options.map((o, i) => {
            const chosen = item.selected.includes(i)
            return (
              <li
                key={i}
                className={cn(
                  'flex items-start gap-3 rounded-lg border px-4 py-2',
                  o.correct && 'border-emerald-500/60 bg-emerald-500/10',
                  chosen && !o.correct && 'border-destructive/60 bg-destructive/10',
                )}
              >
                <span className="mt-0.5 w-4 shrink-0">
                  {o.correct ? (
                    <Check className="size-4 text-emerald-600" />
                  ) : chosen ? (
                    <X className="size-4 text-destructive" />
                  ) : null}
                </span>
                <span className="flex-1">
                  <Markdown>{o.text}</Markdown>
                  {(chosen || o.correct) && o.explanation && (
                    <span className="mt-0.5 block text-xs text-muted-foreground">{o.explanation}</span>
                  )}
                </span>
                {chosen && <span className="shrink-0 text-xs text-muted-foreground">your pick</span>}
              </li>
            )
          })}
        </ul>
      ) : (
        <div className="mt-4">
          <div className="mb-1 text-xs font-medium text-muted-foreground">Your answer</div>
          {item.answer_text.trim() ? (
            <div
              className={cn(
                'rounded-md bg-muted/50 px-3 py-2 text-sm whitespace-pre-wrap',
                q.type === 'code' && 'font-mono',
              )}
            >
              {item.answer_text}
            </div>
          ) : (
            <div className="text-sm text-muted-foreground italic">No answer</div>
          )}
        </div>
      )}

      {q.rubric && q.rubric.length > 0 && !choice && (
        <table className="mt-4 w-full text-sm">
          <tbody>
            {q.rubric.map((r, i) => {
              const a = item.rubric_awarded?.[i]
              return (
                <tr key={i} className="border-t align-top">
                  <td className="w-16 py-1.5 pr-3 tabular-nums">
                    <span
                      className={cn(
                        a && a.points >= r.points
                          ? 'text-emerald-600'
                          : a?.points
                            ? 'text-amber-600'
                            : 'text-destructive',
                      )}
                    >
                      {a?.points ?? 0}
                    </span>
                    <span className="text-muted-foreground">/{r.points}</span>
                  </td>
                  <td className="py-1.5">
                    {r.criterion}
                    {a?.comment && <div className="text-xs text-muted-foreground">{a.comment}</div>}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}

      {item.feedback && !choice && (
        <div className="mt-4 rounded-md border-l-2 border-primary bg-primary/5 px-3 py-2 text-sm">
          <Markdown>{item.feedback}</Markdown>
        </div>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-2 text-xs">
        {!choice && q.answer && (
          <button
            type="button"
            className="inline-flex items-center gap-1 text-muted-foreground hover:text-foreground"
            onClick={() => setShowAnswer((s) => !s)}
          >
            <ChevronDown className={cn('size-3.5 transition', showAnswer && 'rotate-180')} /> Model answer
          </button>
        )}
        {choice && item.selected.length === 0 && <span className="text-destructive">Not answered.</span>}
        {choice && q.explanation && <span className="text-muted-foreground">{q.explanation}</span>}
        <span className="ml-auto flex flex-wrap gap-1">
          {q.sources?.map((s, i) => (
            <Link
              key={i}
              to={`/lectures/${s.lecture_id}?t=${s.start_s}`}
              className="rounded-full border px-2 py-0.5 text-primary hover:bg-primary/10"
            >
              {s.label}
            </Link>
          ))}
        </span>
      </div>
      {showAnswer && (
        <div className="mt-2 rounded-md bg-muted/40 px-3 py-2 text-sm">
          {q.solution_steps && q.solution_steps.length > 0 && (
            <ol className="mb-2 list-decimal space-y-1 pl-5">
              {q.solution_steps.map((s, i) => (
                <li key={i}>
                  <Markdown>{s}</Markdown>
                </li>
              ))}
            </ol>
          )}
          <Markdown>{q.answer ?? ''}</Markdown>
        </div>
      )}
    </QuestionCard>
  )
}
