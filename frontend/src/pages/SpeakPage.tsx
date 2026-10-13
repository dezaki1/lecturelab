import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  CheckCircle2,
  GraduationCap,
  Lightbulb,
  Loader2,
  Mic,
  Play,
  Presentation,
  Trash2,
  Volume2,
  VolumeX,
  XCircle,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { toast } from 'sonner'

import { Markdown } from '@/components/Markdown'
import { VoiceAnswer } from '@/components/VoiceAnswer'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useEvent } from '@/hooks/useEvents'
import { api } from '@/lib/api'
import { formatDateTime } from '@/lib/dates'
import type { OralResult, SpeakRef, SpeakSession, SpeakTurn, TeachResult } from '@/lib/types'
import { cn } from '@/lib/utils'

const GRADE = ['', 'Sehr gut', 'Gut', 'Befriedigend', 'Genügend', 'Nicht genügend']

export function SpeakPage() {
  const { id } = useParams()
  return id ? <SessionView id={Number(id)} /> : <SpeakHome />
}

// Setup + history -----------------------------------------------------------------------------

function SpeakHome() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [mode, setMode] = useState<'teach' | 'oral'>('teach')
  const { data: subjects = [] } = useQuery({ queryKey: ['subjects'], queryFn: api.subjects })
  const [picked, setPicked] = useState<string | null>(null)
  const subjectId = Number(picked ?? subjects.find((s) => s.lecture_count)?.id ?? subjects[0]?.id ?? 0)
  const { data: topics = [], isLoading } = useQuery({
    queryKey: ['speak-topics', subjectId],
    queryFn: () => api.speakTopics(subjectId),
    enabled: subjectId > 0,
  })
  const { data: voice } = useQuery({ queryKey: ['voice-settings'], queryFn: api.voiceSettings, retry: false })
  const { data: history = [] } = useQuery({
    queryKey: ['speak-sessions'],
    queryFn: () => api.speakSessions(),
  })
  const [chosen, setChosen] = useState<string[]>([])
  const [questions, setQuestions] = useState('3')
  const [speakAloud, setSpeakAloud] = useState(true)
  useEffect(() => setChosen([]), [subjectId, mode])

  const start = useMutation({
    mutationFn: () =>
      api.startSpeak({
        subject_id: subjectId,
        mode,
        topics: chosen,
        questions: Number(questions),
        voice: speakAloud,
      }),
    onSuccess: (s) => {
      qc.invalidateQueries({ queryKey: ['speak-sessions'] })
      navigate(`/speak/${s.id}`)
    },
    onError: (e: Error) => toast.error(e.message),
  })
  const remove = useMutation({
    mutationFn: api.deleteSpeak,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['speak-sessions'] }),
  })
  const toggle = (name: string) =>
    setChosen((c) =>
      mode === 'teach' ? [name] : c.includes(name) ? c.filter((x) => x !== name) : [...c, name],
    )
  const canStart = subjectId > 0 && (mode === 'oral' || chosen.length === 1)

  return (
    <div className="mx-auto max-w-4xl px-4 py-10 sm:px-8">
      <h1 className="text-2xl font-semibold tracking-tight">Oral practice</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        Say it out loud: explain a topic in your own words, or sit an oral exam with follow-up questions.
        Answer by voice or by typing.
      </p>

      <section className="mt-6 space-y-5 rounded-lg border bg-card p-5">
        <Tabs value={mode} onValueChange={(v) => setMode(v as 'teach' | 'oral')}>
          <TabsList>
            <TabsTrigger value="teach">
              <Presentation /> Teach-back
            </TabsTrigger>
            <TabsTrigger value="oral">
              <GraduationCap /> Oral exam
            </TabsTrigger>
          </TabsList>
        </Tabs>
        <p className="text-sm text-muted-foreground">
          {mode === 'teach'
            ? 'Explain one topic as if teaching a classmate. You get what you covered, what is missing and what was wrong, with links to the lecture, and one follow-up question.'
            : 'The examiner asks a few questions and follows up when an answer is thin, like the real thing. You get a grade and feedback at the end.'}
        </p>

        <div className="flex flex-wrap items-end gap-4">
          <div className="space-y-1.5">
            <Label htmlFor="speak-subject">Subject</Label>
            <Select value={subjectId ? String(subjectId) : undefined} onValueChange={setPicked}>
              <SelectTrigger id="speak-subject" className="w-72">
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
          {mode === 'oral' && (
            <div className="space-y-1.5">
              <Label htmlFor="speak-questions">Questions</Label>
              <Select value={questions} onValueChange={setQuestions}>
                <SelectTrigger id="speak-questions" className="w-32">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {[2, 3, 4, 5, 6].map((n) => (
                    <SelectItem key={n} value={String(n)}>
                      {n} (~{n * 3} min)
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
          <div className="flex items-center gap-2 pb-2">
            <Switch id="speak-aloud" checked={speakAloud} onCheckedChange={setSpeakAloud} />
            <Label htmlFor="speak-aloud">Read questions aloud</Label>
          </div>
        </div>
        {speakAloud && voice && !voice.tts_ready && (
          <p className="text-xs text-amber-600 dark:text-amber-400">
            No voice installed yet: download one in{' '}
            <Link to="/settings" className="underline">
              Settings → Voice
            </Link>{' '}
            (about 60 MB). Until then questions are shown as text.
          </p>
        )}

        <div className="space-y-2">
          <Label>
            {mode === 'teach' ? 'Topic to explain' : 'Topics (optional: none picked = the whole course)'}
          </Label>
          {isLoading ? (
            <Loader2 className="size-4 animate-spin text-muted-foreground" />
          ) : topics.length ? (
            <div className="flex flex-wrap gap-1.5">
              {topics.map((t) => (
                <button
                  key={t.name}
                  type="button"
                  onClick={() => toggle(t.name)}
                  aria-pressed={chosen.includes(t.name)}
                  className={cn(
                    'rounded-full border px-3 py-1 text-sm transition-colors',
                    chosen.includes(t.name)
                      ? 'border-primary bg-primary text-primary-foreground'
                      : 'hover:bg-accent',
                  )}
                  title={
                    t.strength != null ? `Strength ${Math.round(t.strength * 100)}%` : 'Not practised yet'
                  }
                >
                  {t.status === 'weak' && <span className="mr-1 text-amber-500">●</span>}
                  {t.name}
                </button>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">
              No topics yet: generate study notes for a lecture first.
            </p>
          )}
          {topics.some((t) => t.status === 'weak') && (
            <p className="text-xs text-muted-foreground">
              <span className="text-amber-500">●</span> weak topics from your reviews and answers
            </p>
          )}
        </div>

        <Button onClick={() => start.mutate()} disabled={!canStart || start.isPending}>
          {start.isPending ? <Loader2 className="animate-spin" /> : <Mic />}{' '}
          {mode === 'teach' ? 'Start explaining' : 'Start the exam'}
        </Button>
      </section>

      {history.length > 0 && (
        <section className="mt-6 rounded-lg border bg-card">
          <h2 className="px-4 pt-4 text-sm font-medium">Past sessions</h2>
          <ul className="mt-2 divide-y">
            {history.map((h) => (
              <li key={h.id} className="flex items-center gap-3 px-4 py-2 text-sm">
                <Badge variant="outline" className="shrink-0">
                  {h.mode === 'teach' ? 'Teach-back' : 'Oral exam'}
                </Badge>
                <Link to={`/speak/${h.id}`} className="min-w-0 flex-1 truncate hover:underline">
                  {h.title} <span className="text-muted-foreground">· {h.subject_name}</span>
                </Link>
                <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
                  {h.status === 'active'
                    ? 'in progress'
                    : h.grade
                      ? `${h.grade} · ${Math.round(h.score ?? 0)}%`
                      : h.score != null
                        ? `${Math.round(h.score)}%`
                        : 'ended'}
                </span>
                <span className="hidden shrink-0 text-xs text-muted-foreground sm:inline">
                  {formatDateTime(h.created_at)}
                </span>
                <Button
                  size="icon-xs"
                  variant="ghost"
                  aria-label="Delete"
                  onClick={() => remove.mutate(h.id)}
                >
                  <Trash2 />
                </Button>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}

// Session -----------------------------------------------------------------------------------

function SessionView({ id }: { id: number }) {
  const qc = useQueryClient()
  const { data: s } = useQuery({ queryKey: ['speak', id], queryFn: () => api.speakSession(id) })
  const [muted, setMuted] = useState(false)
  const audio = useRef<HTMLAudioElement | null>(null)
  const played = useRef<number>(-1)
  const bottom = useRef<HTMLDivElement>(null)
  const set = (d: SpeakSession) => qc.setQueryData(['speak', id], d)
  useEvent('speak', (d: { id: number }) => d.id === id && qc.invalidateQueries({ queryKey: ['speak', id] }))

  const play = (index: number) => {
    audio.current?.pause()
    const a = new Audio(api.turnAudioUrl(id, index))
    audio.current = a
    a.play().catch(() => {}) // no voice installed (409) or autoplay blocked: the text is on screen
  }
  useEffect(() => () => audio.current?.pause(), [])

  // Read each new examiner turn aloud once.
  useEffect(() => {
    if (!s) return
    const last = s.turns.length - 1
    if (played.current === -1 && s.status !== 'active') played.current = last
    const turn = s.turns[last]
    if (last > played.current && turn?.role === 'examiner') {
      played.current = last
      if (s.config.voice && !muted) play(last)
    } else if (last > played.current) played.current = last
    bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [s?.turns.length]) // eslint-disable-line react-hooks/exhaustive-deps

  const send = useMutation({
    mutationFn: (v: { text: string; spoken: boolean }) => api.speakAnswer(id, v.text, v.spoken),
    onSuccess: (d) => {
      set(d)
      qc.invalidateQueries({ queryKey: ['speak-sessions'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })
  const finish = useMutation({
    mutationFn: () => api.finishSpeak(id),
    onSuccess: (d) => {
      set(d)
      qc.invalidateQueries({ queryKey: ['speak-sessions'] })
    },
    onError: (e: Error) => toast.error(e.message),
  })
  if (!s) return null
  const active = s.status === 'active'
  const waiting = send.isPending || finish.isPending
  const oral = s.mode === 'oral'

  return (
    <div className="mx-auto max-w-3xl px-4 py-8 sm:px-8">
      <div className="flex flex-wrap items-center gap-2">
        <Link to="/speak" className="text-sm text-muted-foreground hover:underline">
          Oral practice
        </Link>
        <span className="text-muted-foreground">/</span>
        <Badge variant="outline">{oral ? 'Oral exam' : 'Teach-back'}</Badge>
        <h1 className="min-w-0 flex-1 truncate font-semibold">{s.title}</h1>
        {s.config.voice && (
          <Button
            size="icon-sm"
            variant="ghost"
            aria-label={muted ? 'Unmute examiner' : 'Mute examiner'}
            onClick={() => {
              setMuted(!muted)
              audio.current?.pause()
            }}
          >
            {muted ? <VolumeX /> : <Volume2 />}
          </Button>
        )}
        {active && (
          <Button size="sm" variant="outline" onClick={() => finish.mutate()} disabled={waiting}>
            {finish.isPending && <Loader2 className="animate-spin" />}
            {oral ? 'End exam' : 'Finish'}
          </Button>
        )}
      </div>

      <ol className="mt-6 space-y-4">
        {s.turns.map((t, i) => (
          <Turn key={i} turn={t} onPlay={() => play(i)} showRating={!active} />
        ))}
        {waiting && (
          <li className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />
            {finish.isPending
              ? 'Evaluating…'
              : oral
                ? 'The examiner is thinking…'
                : 'Checking your explanation against the lecture…'}
          </li>
        )}
      </ol>

      {active && !waiting && s.turns.at(-1)?.role === 'examiner' && (
        <div className="mt-6">
          <VoiceAnswer
            subjectId={s.subject_id}
            busy={waiting}
            onSend={(text, spoken) => send.mutate({ text, spoken })}
          />
        </div>
      )}

      {!active && oral && s.result.grade != null && <OralResultView r={s.result as OralResult} />}
      {!active && (
        <div className="mt-6 flex gap-2">
          <Button variant="outline" asChild>
            <Link to="/speak">Practise again</Link>
          </Button>
          {s.cost_usd > 0 && (
            <span className="self-center text-xs text-muted-foreground">cost ${s.cost_usd.toFixed(3)}</span>
          )}
        </div>
      )}
      <div ref={bottom} />
    </div>
  )
}

function Turn({ turn: t, onPlay, showRating }: { turn: SpeakTurn; onPlay: () => void; showRating: boolean }) {
  if (t.role === 'coach' && t.data) return <TeachFeedback r={t.data} />
  const student = t.role === 'student'
  return (
    <li className={cn('flex', student && 'justify-end')}>
      <div
        className={cn(
          'max-w-[85%] rounded-2xl px-4 py-2.5 text-sm',
          student ? 'rounded-br-sm bg-primary text-primary-foreground' : 'rounded-bl-sm border bg-card',
        )}
      >
        {!student && (
          <div className="mb-1 flex items-center gap-2 text-xs text-muted-foreground">
            {t.kind === 'follow_up' ? 'Follow-up' : t.kind === 'end' ? 'Examiner' : t.topic || 'Examiner'}
            <button type="button" className="hover:text-foreground" aria-label="Play" onClick={onPlay}>
              <Play className="size-3" />
            </button>
          </div>
        )}
        <p className="whitespace-pre-wrap">{t.text}</p>
        {student && (t.spoken || (showRating && t.rating != null)) && (
          <div className="mt-1 flex items-center justify-end gap-2 text-[11px] opacity-75">
            {t.spoken && (
              <span className="flex items-center gap-0.5">
                <Mic className="size-3" /> spoken
              </span>
            )}
            {showRating && t.rating != null && <span>{t.rating}/10</span>}
          </div>
        )}
      </div>
    </li>
  )
}

function Refs({ refs }: { refs: SpeakRef[] }) {
  if (!refs.length) return null
  return (
    <span className="ml-1 inline-flex flex-wrap gap-1">
      {refs.slice(0, 3).map((r) => (
        <Link
          key={`${r.lecture_id}-${r.start_s}`}
          to={`/lectures/${r.lecture_id}?t=${Math.floor(r.start_s)}`}
          title={r.title}
          className="rounded bg-muted px-1 font-mono text-[11px] text-muted-foreground hover:text-foreground"
        >
          {r.label}
        </Link>
      ))}
    </span>
  )
}

function ScoreRing({ score, label }: { score: number; label?: string }) {
  const color = score >= 80 ? 'text-emerald-500' : score >= 60 ? 'text-amber-500' : 'text-red-500'
  return (
    <div className="relative size-16 shrink-0">
      <svg viewBox="0 0 36 36" className="size-16 -rotate-90">
        <circle cx="18" cy="18" r="15.9" fill="none" className="stroke-muted" strokeWidth="3" />
        <circle
          cx="18"
          cy="18"
          r="15.9"
          fill="none"
          strokeWidth="3"
          strokeLinecap="round"
          strokeDasharray={`${score} 100`}
          className={cn('stroke-current', color)}
        />
      </svg>
      <span className="absolute inset-0 flex flex-col items-center justify-center text-sm font-semibold tabular-nums">
        {label ?? `${Math.round(score)}`}
      </span>
    </div>
  )
}

function Problems({
  missing,
  misconceptions,
}: {
  missing: TeachResult['missing']
  misconceptions: TeachResult['misconceptions']
}) {
  return (
    <>
      {misconceptions.length > 0 && (
        <div>
          <h3 className="text-xs font-medium text-muted-foreground">Not quite right</h3>
          <ul className="mt-1 space-y-1.5">
            {misconceptions.map((m, i) => (
              <li key={i} className="flex gap-1.5">
                <XCircle className="mt-0.5 size-3.5 shrink-0 text-red-500" />
                <span>
                  <span className="text-muted-foreground line-through decoration-red-500/60">
                    {m.student_said}
                  </span>{' '}
                  → {m.correction}
                  <Refs refs={m.refs} />
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {missing.length > 0 && (
        <div>
          <h3 className="text-xs font-medium text-muted-foreground">Missing</h3>
          <ul className="mt-1 space-y-1">
            {missing.map((m, i) => (
              <li key={i} className="flex gap-1.5">
                <Lightbulb className="mt-0.5 size-3.5 shrink-0 text-amber-500" />
                <span>
                  {m.point}
                  <Refs refs={m.refs} />
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  )
}

function TeachFeedback({ r }: { r: TeachResult }) {
  return (
    <li className="rounded-lg border bg-card p-4 text-sm">
      <div className="flex items-start gap-4">
        <ScoreRing score={r.score} />
        <div className="min-w-0 flex-1">
          <p className="font-medium">{r.verdict}</p>
          <div className="mt-1 text-muted-foreground">
            <Markdown>{r.feedback}</Markdown>
          </div>
        </div>
      </div>
      <div className="mt-3 space-y-3">
        {r.covered.length > 0 && (
          <div>
            <h3 className="text-xs font-medium text-muted-foreground">You covered</h3>
            <ul className="mt-1 space-y-0.5">
              {r.covered.map((c, i) => (
                <li key={i} className="flex gap-1.5">
                  <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-emerald-500" /> {c}
                </li>
              ))}
            </ul>
          </div>
        )}
        <Problems missing={r.missing} misconceptions={r.misconceptions} />
      </div>
    </li>
  )
}

function OralResultView({ r }: { r: OralResult }) {
  return (
    <section className="mt-6 space-y-4 rounded-lg border bg-card p-5 text-sm">
      <div className="flex items-start gap-4">
        <ScoreRing score={r.score} label={String(r.grade)} />
        <div className="min-w-0 flex-1">
          <h2 className="font-semibold">
            Grade {r.grade} ({GRADE[r.grade]}) · {Math.round(r.score)}%
          </h2>
          <div className="mt-1 text-muted-foreground">
            <Markdown>{r.summary}</Markdown>
          </div>
        </div>
      </div>
      <ol className="space-y-2">
        {r.questions.map((q, i) => (
          <li key={i} className="rounded-md border p-3">
            <div className="flex items-baseline gap-2">
              <span className="font-medium">{q.question}</span>
              <span className="ml-auto shrink-0 text-xs tabular-nums text-muted-foreground">
                {q.score}/10
              </span>
            </div>
            <p className="mt-1 text-muted-foreground">
              {q.comment}
              <Refs refs={q.refs} />
            </p>
          </li>
        ))}
      </ol>
      {r.strengths.length > 0 && (
        <div>
          <h3 className="text-xs font-medium text-muted-foreground">Strengths</h3>
          <ul className="mt-1 space-y-0.5">
            {r.strengths.map((x, i) => (
              <li key={i} className="flex gap-1.5">
                <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-emerald-500" /> {x}
              </li>
            ))}
          </ul>
        </div>
      )}
      <Problems missing={r.gaps} misconceptions={r.misconceptions} />
      {r.revise.length > 0 && (
        <p className="text-muted-foreground">
          <span className="font-medium text-foreground">Revise first:</span> {r.revise.join(' · ')}
        </p>
      )}
    </section>
  )
}
