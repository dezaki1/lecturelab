import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FileUp, Loader2, Play, Trash2 } from 'lucide-react'
import { useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { api, uploadSubjectDocument } from '@/lib/api'
import type { ExamConfig, ExamProfile, ExamSummary } from '@/lib/types'
import { cn } from '@/lib/utils'

const TYPE_LABEL: Record<string, string> = {
  mcq: 'Multiple choice',
  multi: 'Multiple answers',
  tf: 'True / false',
  short: 'Short answer',
  long: 'Long answer',
  calculation: 'Calculation',
  code: 'Code',
}
const pct = (score: number | null, total: number) =>
  score == null || !total ? '—' : `${Math.round((score / total) * 100)}%`
const when = (iso: string) =>
  new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })

export function ExamsPage() {
  const { data: subjects = [] } = useQuery({ queryKey: ['subjects'], queryFn: api.subjects })
  const [params, setParams] = useSearchParams()
  const subjectId = Number(params.get('subject') ?? subjects[0]?.id ?? 0)
  const { data: profile } = useQuery({
    queryKey: ['exam-profile', subjectId],
    queryFn: () => api.examProfile(subjectId),
    enabled: subjectId > 0,
  })
  const { data: history = [] } = useQuery({
    queryKey: ['exams', subjectId],
    queryFn: () => api.exams(subjectId),
    enabled: subjectId > 0,
  })

  return (
    <div className="mx-auto max-w-5xl px-8 py-10">
      <h1 className="text-2xl font-semibold tracking-tight">Exam simulator</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        Sit a timed practice exam built from your lectures, in the style of the real exams. Open answers are
        graded against a rubric with partial credit and feedback.
      </p>
      <div className="mt-6 flex items-center gap-2">
        <Select
          value={subjectId ? String(subjectId) : undefined}
          onValueChange={(v) => setParams({ subject: v })}
        >
          <SelectTrigger className="w-72">
            <SelectValue placeholder="Pick a subject" />
          </SelectTrigger>
          <SelectContent>
            {subjects.map((s) => (
              <SelectItem key={s.id} value={String(s.id)}>
                {s.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      {subjectId > 0 && profile && (
        <div className="mt-6 grid gap-4 lg:grid-cols-[3fr_2fr]">
          <Setup subjectId={subjectId} profile={profile} />
          <PastExams subjectId={subjectId} profile={profile} />
        </div>
      )}
      {history.length > 0 && <History items={history} />}
    </div>
  )
}

function Setup({ subjectId, profile }: { subjectId: number; profile: ExamProfile }) {
  const navigate = useNavigate()
  const { data: hub } = useQuery({ queryKey: ['hub', subjectId], queryFn: () => api.hub(subjectId) })
  const p = profile.profile
  const [duration, setDuration] = useState<string>('')
  const [types, setTypes] = useState<string[]>([])
  const [topics, setTopics] = useState<string[]>([])
  const [difficulty, setDifficulty] = useState<ExamConfig['difficulty']>('mixed')
  const [allowNew, setAllowNew] = useState(true)
  const banked = Object.values(profile.bank).reduce((a, b) => a + b, 0)
  const create = useMutation({
    mutationFn: () =>
      api.createExam(subjectId, {
        topics,
        types: types.length ? types : Object.keys(TYPE_LABEL),
        difficulty,
        duration_min: duration ? Number(duration) : undefined,
        allow_new: allowNew,
      }),
    onSuccess: (e) => navigate(`/exams/${e.id}`),
    onError: (e: Error) => toast.error(e.message),
  })
  const toggle = (list: string[], set: (v: string[]) => void, v: string) =>
    set(list.includes(v) ? list.filter((x) => x !== v) : [...list, v])

  return (
    <section className="space-y-4 rounded-lg border bg-card p-5">
      <h2 className="font-semibold">New practice exam</h2>
      <div className="flex flex-wrap items-end gap-4">
        <div className="space-y-1.5">
          <Label htmlFor="duration">Duration (minutes)</Label>
          <Input
            id="duration"
            type="number"
            min={5}
            max={300}
            placeholder={String(p?.duration_min ?? 60)}
            value={duration}
            onChange={(e) => setDuration(e.target.value)}
            className="h-8 w-28"
          />
        </div>
        <div className="space-y-1.5">
          <Label>Difficulty</Label>
          <Select value={difficulty} onValueChange={(v) => setDifficulty(v as ExamConfig['difficulty'])}>
            <SelectTrigger size="sm" className="w-32">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="mixed">Mixed</SelectItem>
              <SelectItem value="easy">Easy</SelectItem>
              <SelectItem value="medium">Medium</SelectItem>
              <SelectItem value="hard">Hard</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>
      <div className="space-y-1.5">
        <Label>
          Question types{' '}
          {types.length === 0 && <span className="font-normal text-muted-foreground">(all)</span>}
        </Label>
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(TYPE_LABEL).map(([t, label]) => (
            <Chip key={t} active={types.includes(t)} onClick={() => toggle(types, setTypes, t)}>
              {label}
              {profile.bank[t] ? <span className="ml-1 text-muted-foreground">{profile.bank[t]}</span> : null}
            </Chip>
          ))}
        </div>
      </div>
      {hub && hub.topics.length > 0 && (
        <div className="space-y-1.5">
          <Label>
            Topics {topics.length === 0 && <span className="font-normal text-muted-foreground">(all)</span>}
          </Label>
          <div className="flex flex-wrap gap-1.5">
            {hub.topics.map((t) => (
              <Chip
                key={t.name}
                active={topics.includes(t.name)}
                onClick={() => toggle(topics, setTopics, t.name)}
              >
                {t.name}
                {t.mastery != null && (
                  <span className="ml-1 text-muted-foreground">{Math.round(t.mastery * 100)}%</span>
                )}
              </Chip>
            ))}
          </div>
        </div>
      )}
      <div className="flex items-center justify-between gap-4">
        <div>
          <Label htmlFor="allow-new">Write new questions when needed</Label>
          <p className="text-xs text-muted-foreground">
            {banked} questions in the bank. New ones follow the past exams&apos; style (uses Claude).
          </p>
        </div>
        <Switch
          id="allow-new"
          checked={allowNew}
          onCheckedChange={setAllowNew}
          disabled={!profile.can_generate}
        />
      </div>
      <Button onClick={() => create.mutate()} disabled={create.isPending}>
        {create.isPending ? <Loader2 className="animate-spin" /> : <Play />} Build exam
      </Button>
    </section>
  )
}

function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'rounded-full border px-2.5 py-0.5 text-xs transition',
        active
          ? 'border-primary bg-primary/10 text-foreground'
          : 'text-muted-foreground hover:border-foreground/30',
      )}
    >
      {children}
    </button>
  )
}

function PastExams({ subjectId, profile }: { subjectId: number; profile: ExamProfile }) {
  const qc = useQueryClient()
  const input = useRef<HTMLInputElement>(null)
  const upload = useMutation({
    mutationFn: (f: File) => uploadSubjectDocument(subjectId, f, 'past_exam'),
    onSuccess: () => {
      toast.success('Reading the exam… this takes a minute')
      qc.invalidateQueries({ queryKey: ['exam-profile', subjectId] })
    },
    onError: (e: Error) => toast.error(e.message),
  })
  const p = profile.profile
  return (
    <section className="space-y-3 rounded-lg border bg-card p-5 text-sm">
      <div className="flex items-center">
        <h2 className="font-semibold">Past exams</h2>
        <input
          ref={input}
          type="file"
          accept="application/pdf,.pdf"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) upload.mutate(f)
            e.target.value = ''
          }}
        />
        <Button size="xs" variant="outline" className="ml-auto" onClick={() => input.current?.click()}>
          {upload.isPending ? <Loader2 className="animate-spin" /> : <FileUp />} Add PDF
        </Button>
      </div>
      {profile.past_exams.length === 0 ? (
        <p className="text-muted-foreground">
          Add old exams (PDF, scans work too) and practice exams will match their format, style and topic
          weighting.
        </p>
      ) : (
        <ul className="space-y-1">
          {profile.past_exams.map((e) => (
            <li key={e.id} className="flex justify-between gap-2">
              <span className="truncate">{e.title}</span>
              <span className="shrink-0 text-xs text-muted-foreground">
                {e.analysed ? `${e.questions} questions` : e.has_text ? 'analysing…' : 'reading…'}
              </span>
            </li>
          ))}
        </ul>
      )}
      {p && (
        <div className="space-y-1.5 border-t pt-3 text-xs text-muted-foreground">
          {p.duration_min && p.total_points && (
            <p>
              Typical exam: {p.duration_min} min, {p.total_points} points
            </p>
          )}
          <p>
            Types:{' '}
            {Object.entries(p.types)
              .map(([t, share]) => `${TYPE_LABEL[t] ?? t} ${Math.round(share * 100)}%`)
              .join(' · ')}
          </p>
          {p.topics.length > 0 && (
            <p>
              Most weight:{' '}
              {p.topics
                .slice(0, 5)
                .map((t) => t.topic)
                .join(', ')}
            </p>
          )}
          {p.style_notes[0] && <p className="italic">“{p.style_notes[0]}”</p>}
        </div>
      )}
    </section>
  )
}

function History({ items }: { items: ExamSummary[] }) {
  const qc = useQueryClient()
  const del = useMutation({
    mutationFn: api.deleteExam,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['exams'] }),
    onError: (e: Error) => toast.error(e.message),
  })
  const graded = items.filter((e) => e.status === 'graded' && e.score != null).reverse()
  return (
    <section className="mt-8">
      <h2 className="text-lg font-semibold">History</h2>
      {graded.length > 1 && (
        <div className="mt-2 flex h-16 items-end gap-1" aria-label="Scores over time">
          {graded.map((e) => (
            <div
              key={e.id}
              className="w-6 rounded-t-sm bg-primary/70"
              style={{ height: `${Math.max(4, ((e.score ?? 0) / (e.total_points || 1)) * 100)}%` }}
              title={`${e.title}: ${pct(e.score, e.total_points)}`}
            />
          ))}
        </div>
      )}
      <ul className="mt-3 divide-y rounded-lg border bg-card text-sm">
        {items.map((e) => (
          <li key={e.id} className="group flex items-center gap-3 px-4 py-2.5">
            <Link to={`/exams/${e.id}`} className="min-w-0 flex-1 truncate font-medium hover:underline">
              {e.title}
            </Link>
            <span className="text-xs text-muted-foreground">{when(e.created_at)}</span>
            <span className="w-40 text-right text-xs tabular-nums">
              {e.status === 'graded'
                ? `${e.score} / ${e.total_points} · ${pct(e.score, e.total_points)}`
                : ({
                    building: 'preparing…',
                    ready: 'not started',
                    in_progress: 'in progress',
                    grading: 'grading…',
                  }[e.status as string] ?? e.status)}
            </span>
            <Button
              size="icon-xs"
              variant="ghost"
              className="opacity-0 group-hover:opacity-100"
              aria-label="Delete exam"
              disabled={e.status === 'building' || e.status === 'grading'}
              onClick={() => del.mutate(e.id)}
            >
              <Trash2 />
            </Button>
          </li>
        ))}
      </ul>
    </section>
  )
}
