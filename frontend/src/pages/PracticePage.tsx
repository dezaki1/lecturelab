import { useMutation, useQuery } from '@tanstack/react-query'
import { Check, ChevronRight, ExternalLink, Lightbulb, RotateCcw, Star, Target, X } from 'lucide-react'
import { useRef, useState } from 'react'
import { toast } from 'sonner'

import { Markdown } from '@/components/Markdown'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { api } from '@/lib/api'
import type { AttemptResult, PracticeItem } from '@/lib/types'
import { cn } from '@/lib/utils'

const CHOICE = new Set(['mcq', 'multi', 'tf'])
const TYPE_LABEL: Record<string, string> = {
  mcq: 'Multiple choice', multi: 'Multiple select', tf: 'True/false', short: 'Short answer',
  long: 'Long answer', calculation: 'Calculation', code: 'Code', derivation: 'Derivation',
  conceptual: 'Conceptual', coding: 'Coding',
} // prettier-ignore

type Result = { item: PracticeItem; score: number; max: number }

export function PracticePage() {
  const { data: subjects = [] } = useQuery({ queryKey: ['subjects'], queryFn: api.subjects })
  const [subject, setSubject] = useState('all')
  const [kind, setKind] = useState('exam')
  const [mode, setMode] = useState('mixed')
  const [count, setCount] = useState('10')
  const [session, setSession] = useState<PracticeItem[] | null>(null)
  const [index, setIndex] = useState(0)
  const [results, setResults] = useState<Result[]>([])

  const start = useMutation({
    mutationFn: () =>
      api.practiceQuestions({
        subject_id: subject === 'all' ? undefined : subject,
        kind,
        mode,
        limit: count,
      }),
    onSuccess: (items) => {
      if (!items.length) return toast.info('No questions yet. Generate study material for a lecture first.')
      setSession(items)
      setIndex(0)
      setResults([])
    },
    onError: (e: Error) => toast.error(e.message),
  })

  if (session && index < session.length) {
    return (
      <div className="mx-auto max-w-3xl px-8 py-8">
        <div className="mb-4 flex items-center gap-3 text-sm text-muted-foreground">
          <Target className="size-4" />
          Question {index + 1} of {session.length}
          <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
            <div className="h-full bg-primary" style={{ width: `${(index / session.length) * 100}%` }} />
          </div>
          <button className="hover:text-foreground" onClick={() => setSession(null)}>
            End
          </button>
        </div>
        <QuestionPractice
          key={session[index].id}
          item={session[index]}
          onDone={(score, max) => {
            setResults((r) => [...r, { item: session[index], score, max }])
            setIndex((i) => i + 1)
          }}
        />
      </div>
    )
  }

  if (session) {
    const score = results.reduce((s, r) => s + r.score, 0)
    const max = results.reduce((s, r) => s + r.max, 0)
    return (
      <div className="mx-auto max-w-2xl px-8 py-12">
        <h1 className="text-2xl font-semibold">
          {score} / {max} points{max ? ` (${Math.round((score / max) * 100)}%)` : ''}
        </h1>
        <ul className="mt-6 divide-y rounded-lg border bg-card">
          {results.map((r, i) => (
            <li key={i} className="flex items-center gap-3 px-4 py-2.5 text-sm">
              {r.score >= r.max ? (
                <Check className="size-4 text-emerald-500" />
              ) : r.score > 0 ? (
                <span className="size-4 text-center text-amber-500">~</span>
              ) : (
                <X className="size-4 text-destructive" />
              )}
              <span className="flex-1 truncate">
                {r.item.question.title || r.item.question.prompt.slice(0, 90)}
              </span>
              <span className="tabular-nums text-muted-foreground">
                {r.score}/{r.max}
              </span>
            </li>
          ))}
        </ul>
        <div className="mt-6 flex gap-2">
          <Button onClick={() => start.mutate()}>
            <RotateCcw /> Another round
          </Button>
          <Button variant="ghost" onClick={() => setSession(null)}>
            Change settings
          </Button>
        </div>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-xl px-8 py-12">
      <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
        <Target className="size-5 text-primary" /> Practice
      </h1>
      <p className="mt-1 text-sm text-muted-foreground">
        Answer exam questions from your lectures. Multiple choice is graded automatically; for open questions
        you compare with the model answer and tick the rubric points you earned.
      </p>
      <div className="mt-6 grid gap-4 rounded-lg border bg-card p-5">
        <Field label="Subject">
          <Select value={subject} onValueChange={setSubject}>
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All subjects</SelectItem>
              {subjects.map((s) => (
                <SelectItem key={s.id} value={String(s.id)}>
                  {s.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <div className="grid grid-cols-3 gap-3">
          <Field label="Questions">
            <Select value={kind} onValueChange={setKind}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="exam">Exam questions</SelectItem>
                <SelectItem value="exercise">Exercises</SelectItem>
              </SelectContent>
            </Select>
          </Field>
          <Field label="Order">
            <Select value={mode} onValueChange={setMode}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="mixed">Random</SelectItem>
                <SelectItem value="weakest">Weakest first</SelectItem>
              </SelectContent>
            </Select>
          </Field>
          <Field label="How many">
            <Select value={count} onValueChange={setCount}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {['5', '10', '20', '30'].map((n) => (
                  <SelectItem key={n} value={n}>
                    {n}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
        </div>
        <Button onClick={() => start.mutate()} disabled={start.isPending}>
          Start
        </Button>
      </div>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-1.5">
      <Label>{label}</Label>
      {children}
    </div>
  )
}

function QuestionPractice({
  item,
  onDone,
}: {
  item: PracticeItem
  onDone: (score: number, max: number) => void
}) {
  const q = item.question
  const choice = CHOICE.has(q.type) && q.options.length > 0
  const [picked, setPicked] = useState<number[]>([])
  const [answer, setAnswer] = useState('')
  const [revealed, setRevealed] = useState(false)
  const [checked, setChecked] = useState<number[]>([])
  const [hints, setHints] = useState(0)
  const [selfScore, setSelfScore] = useState<number | null>(null)
  const [result, setResult] = useState<AttemptResult | null>(null)
  const startedAt = useRef(Date.now())

  const submit = useMutation({
    mutationFn: () =>
      api.submitAttempt(q.id, {
        ...(choice
          ? { selected: picked }
          : q.rubric.length
            ? { answer_text: answer, rubric_checked: checked }
            : { answer_text: answer, self_score: selfScore ?? 0 }),
        duration_ms: Date.now() - startedAt.current,
      }),
    onSuccess: (r) => {
      setResult(r)
      if (!choice) onDone(r.score, r.max_points)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const toggle = (i: number) =>
    setPicked((p) => (q.type === 'multi' ? (p.includes(i) ? p.filter((x) => x !== i) : [...p, i]) : [i]))
  const graded = choice && result != null
  const rubricPoints = checked.reduce((s, i) => s + (q.rubric[i]?.points ?? 0), 0)

  return (
    <article className="rounded-xl border bg-card p-6">
      <header className="mb-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span className="size-2 rounded-full" style={{ background: item.subject_color }} />
        <span>{item.subject_name}</span>
        {item.lecture_number && (
          <a
            href={`/lectures/${item.lecture_id}`}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-0.5 hover:text-foreground"
          >
            L{item.lecture_number} <ExternalLink className="size-3" />
          </a>
        )}
        <Badge variant="outline">{TYPE_LABEL[q.type] ?? q.type}</Badge>
        {q.bloom && <span className="capitalize">{q.bloom}</span>}
        <span>
          {q.points} pt{q.points === 1 ? '' : 's'}
        </span>
        {q.emphasized && <Star className="size-3 fill-amber-400 text-amber-500" />}
        {item.attempts > 0 && (
          <span className="ml-auto">
            last: {item.last_score}/{item.last_max}
          </span>
        )}
      </header>
      {q.title && <h2 className="mb-2 font-semibold">{q.title}</h2>}
      <Markdown>{q.prompt}</Markdown>

      {choice ? (
        <ul className="mt-4 space-y-2">
          {q.options.map((o, i) => {
            const chosen = picked.includes(i)
            const isRight = result?.correct_options.includes(i)
            return (
              <li key={i}>
                <button
                  disabled={graded}
                  onClick={() => toggle(i)}
                  className={cn(
                    'flex w-full items-start gap-3 rounded-lg border px-4 py-3 text-left text-sm transition',
                    !graded && (chosen ? 'border-primary bg-primary/5' : 'hover:bg-muted/50'),
                    graded && isRight && 'border-emerald-500/60 bg-emerald-500/10',
                    graded && chosen && !isRight && 'border-destructive/60 bg-destructive/10',
                  )}
                >
                  <span className="mt-0.5 w-4 shrink-0">
                    {graded ? (
                      isRight ? (
                        <Check className="size-4 text-emerald-600" />
                      ) : chosen ? (
                        <X className="size-4 text-destructive" />
                      ) : null
                    ) : (
                      <ChevronRight
                        className={cn('size-4', chosen ? 'text-primary' : 'text-muted-foreground')}
                      />
                    )}
                  </span>
                  <span className="flex-1">
                    <Markdown>{o.text}</Markdown>
                    {graded && (
                      <span className="mt-1 block text-xs text-muted-foreground">{o.explanation}</span>
                    )}
                  </span>
                </button>
              </li>
            )
          })}
        </ul>
      ) : (
        <>
          {q.hints.slice(0, hints).map((h, i) => (
            <div key={i} className="mt-3 flex gap-2 rounded-md bg-muted/60 px-3 py-2 text-sm">
              <Lightbulb className="mt-0.5 size-4 shrink-0 text-amber-500" />
              <Markdown>{h}</Markdown>
            </div>
          ))}
          <Textarea
            className="mt-4 min-h-32"
            placeholder="Write your answer (optional, it is saved with your attempt)…"
            value={answer}
            disabled={revealed}
            onChange={(e) => setAnswer(e.target.value)}
          />
        </>
      )}

      {graded && (
        <div className="mt-4 flex items-center gap-3">
          <span className={cn('font-medium', result!.correct ? 'text-emerald-600' : 'text-destructive')}>
            {result!.correct ? 'Correct' : 'Not quite'} · {result!.score}/{result!.max_points}
          </span>
          {q.explanation && <span className="text-sm text-muted-foreground">{q.explanation}</span>}
        </div>
      )}

      {revealed && !choice && (
        <div className="mt-5 space-y-4 border-t pt-5 text-sm">
          {q.solution_steps.length > 0 && (
            <ol className="space-y-1.5">
              {q.solution_steps.map((s, i) => (
                <li key={i} className="flex gap-2">
                  <span className="w-5 shrink-0 text-right text-muted-foreground">{i + 1}.</span>
                  <Markdown className="flex-1">{s}</Markdown>
                </li>
              ))}
            </ol>
          )}
          <div>
            <div className="mb-1 font-medium">Model answer</div>
            <Markdown>{q.answer}</Markdown>
          </div>
          {q.rubric.length > 0 ? (
            <div>
              <div className="mb-1 font-medium">
                Which points did you get? ({rubricPoints}/{q.points})
              </div>
              <ul className="space-y-1">
                {q.rubric.map((r, i) => (
                  <li key={i}>
                    <label className="flex cursor-pointer items-start gap-2">
                      <input
                        type="checkbox"
                        className="mt-1"
                        checked={checked.includes(i)}
                        onChange={() =>
                          setChecked((c) => (c.includes(i) ? c.filter((x) => x !== i) : [...c, i]))
                        }
                      />
                      <span className="w-10 shrink-0 text-right tabular-nums text-muted-foreground">
                        {r.points} pt
                      </span>
                      <span>{r.criterion}</span>
                    </label>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <div className="flex items-center gap-2">
              {/* No rubric (exercises): self-assess all or nothing */}
              <span className="font-medium">Did you get it?</span>
              <Button
                size="sm"
                variant={selfScore === q.points ? 'default' : 'outline'}
                onClick={() => setSelfScore(q.points)}
              >
                <Check /> Yes
              </Button>
              <Button
                size="sm"
                variant={selfScore === 0 ? 'default' : 'outline'}
                onClick={() => setSelfScore(0)}
              >
                <X /> No
              </Button>
            </div>
          )}
        </div>
      )}

      <div className="mt-5 flex gap-2">
        {choice && !graded && (
          <Button onClick={() => submit.mutate()} disabled={!picked.length || submit.isPending}>
            Check answer
          </Button>
        )}
        {choice && graded && (
          <Button onClick={() => onDone(result!.score, result!.max_points)}>
            Next <ChevronRight />
          </Button>
        )}
        {!choice && !revealed && (
          <>
            <Button onClick={() => setRevealed(true)}>Show model answer</Button>
            {hints < q.hints.length && (
              <Button variant="outline" onClick={() => setHints(hints + 1)}>
                <Lightbulb /> Hint
              </Button>
            )}
          </>
        )}
        {!choice && revealed && (
          <Button
            onClick={() => submit.mutate()}
            disabled={submit.isPending || (!q.rubric.length && selfScore == null)}
          >
            Save score {q.rubric.length ? `(${rubricPoints}/${q.points})` : ''} and continue <ChevronRight />
          </Button>
        )}
      </div>
    </article>
  )
}
