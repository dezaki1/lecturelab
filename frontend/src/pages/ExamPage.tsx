import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Check, Clock, Loader2, Play, Send, X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router'
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
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { api } from '@/lib/api'
import { formatDuration } from '@/lib/dates'
import type { ExamAnswer, ExamDetail } from '@/lib/types'
import { cn } from '@/lib/utils'

const CHOICE = new Set(['mcq', 'multi', 'tf'])
const SAVE_DELAY_MS = 800

export function ExamPage() {
  const id = Number(useParams().id)
  const { data: exam, error } = useQuery({
    queryKey: ['exam', id],
    queryFn: () => api.exam(id),
    // Building and grading run in the background; poll as a fallback to the live events.
    refetchInterval: (q) => (['building', 'grading'].includes(q.state.data?.status ?? '') ? 3000 : false),
  })
  if (error) return <div className="p-10 text-sm text-destructive">{error.message}</div>
  if (!exam) return null

  return (
    <div className="mx-auto max-w-3xl px-4 sm:px-8 py-10">
      <Link
        to={`/exams?subject=${exam.subject_id}`}
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" /> Exam simulator
      </Link>
      <h1 className="mt-3 text-2xl font-semibold tracking-tight">{exam.title}</h1>
      {exam.status === 'building' && <Waiting text="Preparing your exam… choosing and writing questions." />}
      {exam.status === 'grading' && <Waiting text="Grading your answers…" />}
      {(exam.status === 'failed' || exam.status === 'grading_failed') && <Failed exam={exam} />}
      {exam.status === 'ready' && <Intro exam={exam} />}
      {exam.status === 'in_progress' && <Taking exam={exam} />}
      {exam.status === 'graded' && <Results exam={exam} />}
    </div>
  )
}

function Waiting({ text }: { text: string }) {
  return (
    <p className="mt-8 flex items-center gap-2 text-sm text-muted-foreground">
      <Loader2 className="size-4 animate-spin" /> {text}
    </p>
  )
}

function Failed({ exam }: { exam: ExamDetail }) {
  const qc = useQueryClient()
  const retry = useMutation({
    mutationFn: () => api.regradeExam(exam.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['exam', exam.id] }),
    onError: (e: Error) => toast.error(e.message),
  })
  return (
    <div className="mt-6 rounded-lg border border-destructive/40 bg-destructive/5 p-4 text-sm">
      <p className="text-destructive">{exam.error || 'Something went wrong.'}</p>
      {exam.status === 'grading_failed' && (
        <Button size="sm" variant="outline" className="mt-3" onClick={() => retry.mutate()}>
          Retry grading
        </Button>
      )}
    </div>
  )
}

function Intro({ exam }: { exam: ExamDetail }) {
  const qc = useQueryClient()
  const start = useMutation({
    mutationFn: () => api.startExam(exam.id),
    onSuccess: (d) => qc.setQueryData(['exam', exam.id], d),
    onError: (e: Error) => toast.error(e.message),
  })
  return (
    <div className="mt-6 space-y-4 rounded-lg border bg-card p-5 text-sm">
      <p>
        {exam.answers.length} questions · {exam.total_points} points · {Math.round(exam.time_limit_s / 60)}{' '}
        minutes
      </p>
      <ul className="list-disc space-y-1 pl-5 text-muted-foreground">
        <li>The timer starts when you press Start and keeps running if you leave the page.</li>
        <li>Your answers are saved as you type. When time is up, the exam is submitted automatically.</li>
        <li>Work as in the real exam: no notes, no looking things up.</li>
      </ul>
      <Button onClick={() => start.mutate()} disabled={start.isPending}>
        <Play /> Start exam
      </Button>
    </div>
  )
}

type Draft = { selected: number[]; text: string }

function Taking({ exam }: { exam: ExamDetail }) {
  const qc = useQueryClient()
  const [drafts, setDrafts] = useState<Record<number, Draft>>(() =>
    Object.fromEntries(exam.answers.map((a) => [a.id, { selected: a.selected, text: a.answer_text }])),
  )
  const [confirm, setConfirm] = useState(false)
  const timers = useRef<Record<number, ReturnType<typeof setTimeout>>>({})
  const dirty = useRef<Set<number>>(new Set())
  const submitted = useRef(false)
  const offset = useMemo(() => new Date(exam.server_now).getTime() - Date.now(), [exam.server_now])
  const deadline = exam.deadline ? new Date(exam.deadline).getTime() : 0
  const [now, setNow] = useState(() => Date.now() + offset)
  const remaining = Math.max(0, (deadline - now) / 1000)

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() + offset), 500)
    return () => clearInterval(t)
  }, [offset])

  const save = useCallback(
    async (answerId: number, d: Draft, choice: boolean) => {
      dirty.current.delete(answerId)
      try {
        await api.saveExamAnswer(
          exam.id,
          answerId,
          choice ? { selected: d.selected } : { answer_text: d.text },
        )
      } catch (e) {
        toast.error((e as Error).message)
      }
    },
    [exam.id],
  )

  const flush = useCallback(async () => {
    const pending = [...dirty.current]
    for (const aid of pending) {
      clearTimeout(timers.current[aid])
      await save(aid, drafts[aid], false)
    }
  }, [drafts, save])

  const submit = useMutation({
    mutationFn: async () => {
      submitted.current = true
      await flush()
      return api.submitExam(exam.id)
    },
    onSuccess: (d) => qc.setQueryData(['exam', exam.id], d),
    onError: (e: Error) => {
      submitted.current = false
      toast.error(e.message)
    },
  })

  // Time is up: submit what is there.
  useEffect(() => {
    if (remaining <= 0 && !submitted.current) submit.mutate()
  }, [remaining, submit])

  const setChoice = (a: ExamAnswer, i: number) => {
    const multi = a.question.type === 'multi'
    const cur = drafts[a.id].selected
    const selected = multi ? (cur.includes(i) ? cur.filter((x) => x !== i) : [...cur, i]) : [i]
    const d = { ...drafts[a.id], selected }
    setDrafts((s) => ({ ...s, [a.id]: d }))
    void save(a.id, d, true)
  }
  const setText = (a: ExamAnswer, text: string) => {
    const d = { ...drafts[a.id], text }
    setDrafts((s) => ({ ...s, [a.id]: d }))
    dirty.current.add(a.id)
    clearTimeout(timers.current[a.id])
    timers.current[a.id] = setTimeout(() => void save(a.id, d, false), SAVE_DELAY_MS)
  }

  const answered = exam.answers.filter((a) =>
    CHOICE.has(a.question.type) ? drafts[a.id].selected.length > 0 : drafts[a.id].text.trim(),
  ).length
  const low = remaining < 300

  return (
    <div>
      <div className="sticky top-0 z-10 -mx-4 sm:-mx-8 mt-4 flex items-center gap-3 border-b bg-background/95 px-4 sm:px-8 py-3 backdrop-blur">
        <span
          className={cn(
            'flex items-center gap-1.5 font-mono text-lg tabular-nums',
            low && 'text-destructive',
          )}
        >
          <Clock className="size-4" /> {formatDuration(remaining)}
        </span>
        <span className="text-sm text-muted-foreground">
          {answered} of {exam.answers.length} answered
        </span>
        <Button className="ml-auto" size="sm" onClick={() => setConfirm(true)} disabled={submit.isPending}>
          {submit.isPending ? <Loader2 className="animate-spin" /> : <Send />} Submit
        </Button>
      </div>
      <ol className="mt-6 space-y-6">
        {exam.answers.map((a, k) => (
          <li key={a.id} className="rounded-lg border bg-card p-5">
            <div className="mb-2 flex items-center text-xs text-muted-foreground">
              <span className="font-medium text-foreground">Question {k + 1}</span>
              <span className="ml-auto">
                {a.max_points} point{a.max_points === 1 ? '' : 's'}
              </span>
            </div>
            <Markdown>{a.question.prompt}</Markdown>
            {CHOICE.has(a.question.type) && a.question.options.length > 0 ? (
              <div className="mt-3 space-y-1.5">
                {a.question.type === 'multi' && (
                  <p className="text-xs text-muted-foreground">Select all that apply.</p>
                )}
                {a.question.options.map((o, i) => (
                  <label
                    key={i}
                    className={cn(
                      'flex cursor-pointer items-start gap-2 rounded-md border px-3 py-2 text-sm transition',
                      drafts[a.id].selected.includes(i) ? 'border-primary bg-primary/5' : 'hover:bg-muted/50',
                    )}
                  >
                    <input
                      type={a.question.type === 'multi' ? 'checkbox' : 'radio'}
                      name={`q${a.id}`}
                      className="mt-1 accent-primary"
                      checked={drafts[a.id].selected.includes(i)}
                      onChange={() => setChoice(a, i)}
                    />
                    <Markdown>{o.text}</Markdown>
                  </label>
                ))}
              </div>
            ) : (
              <Textarea
                className="mt-3 min-h-32 font-mono text-sm"
                placeholder={
                  a.question.type === 'code' ? 'Your code…' : 'Your answer… (Markdown and $math$ work)'
                }
                value={drafts[a.id].text}
                onChange={(e) => setText(a, e.target.value)}
              />
            )}
          </li>
        ))}
      </ol>

      <AlertDialog open={confirm} onOpenChange={setConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Submit the exam?</AlertDialogTitle>
            <AlertDialogDescription>
              {answered < exam.answers.length
                ? `${exam.answers.length - answered} question(s) are still unanswered. `
                : ''}
              You can't change your answers afterwards.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep working</AlertDialogCancel>
            <AlertDialogAction onClick={() => submit.mutate()}>Submit</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function Results({ exam }: { exam: ExamDetail }) {
  const pctScore = exam.total_points ? Math.round(((exam.score ?? 0) / exam.total_points) * 100) : 0
  const took =
    exam.started_at && exam.submitted_at
      ? (new Date(exam.submitted_at).getTime() - new Date(exam.started_at).getTime()) / 1000
      : null
  return (
    <div className="mt-6 space-y-6">
      <section className="rounded-lg border bg-card p-5">
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <span className="text-4xl font-semibold tabular-nums">{pctScore}%</span>
          <span className="text-muted-foreground tabular-nums">
            {exam.score} / {exam.total_points} points
          </span>
          {took != null && (
            <span className="text-sm text-muted-foreground">
              in {formatDuration(took)} of {formatDuration(exam.time_limit_s)}
              {exam.config.late && ' (submitted late)'}
            </span>
          )}
        </div>
        {exam.summary_md && (
          <div className="mt-3 text-sm">
            <Markdown>{exam.summary_md}</Markdown>
          </div>
        )}
        {exam.revise.length > 0 && (
          <p className="mt-2 text-sm">
            <span className="font-medium">Revise: </span>
            {exam.revise.join(' · ')}
          </p>
        )}
      </section>
      <ol className="space-y-5">
        {exam.answers.map((a, k) => (
          <ReviewItem key={a.id} n={k + 1} a={a} />
        ))}
      </ol>
    </div>
  )
}

function ReviewItem({ n, a }: { n: number; a: ExamAnswer }) {
  const q = a.question
  const full = (a.score ?? 0) >= a.max_points - 1e-9
  const none = (a.score ?? 0) === 0
  return (
    <li className="rounded-lg border bg-card p-5">
      <div className="mb-2 flex items-center gap-2 text-xs">
        <span className="font-medium">Question {n}</span>
        <span
          className={cn(
            'ml-auto rounded px-1.5 py-px font-medium tabular-nums',
            full
              ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
              : none
                ? 'bg-red-500/10 text-red-700 dark:text-red-300'
                : 'bg-amber-500/10 text-amber-700 dark:text-amber-300',
          )}
        >
          {a.score} / {a.max_points}
        </span>
      </div>
      <Markdown>{q.prompt}</Markdown>

      {CHOICE.has(q.type) && q.options.length > 0 ? (
        <ul className="mt-3 space-y-1.5">
          {q.options.map((o, i) => {
            const picked = a.selected.includes(i)
            return (
              <li
                key={i}
                className={cn(
                  'flex items-start gap-2 rounded-md border px-3 py-2 text-sm',
                  o.correct && 'border-emerald-500/50 bg-emerald-500/5',
                  picked && !o.correct && 'border-red-500/50 bg-red-500/5',
                )}
              >
                {o.correct ? (
                  <Check className="mt-0.5 size-4 shrink-0 text-emerald-600" />
                ) : picked ? (
                  <X className="mt-0.5 size-4 shrink-0 text-red-600" />
                ) : (
                  <span className="size-4 shrink-0" />
                )}
                <div className="min-w-0">
                  <Markdown>{o.text}</Markdown>
                  {(picked || o.correct) && o.explanation && (
                    <p className="mt-0.5 text-xs text-muted-foreground">{o.explanation}</p>
                  )}
                </div>
                {picked && <span className="ml-auto shrink-0 text-xs text-muted-foreground">your pick</span>}
              </li>
            )
          })}
        </ul>
      ) : (
        <>
          <div className="mt-3 rounded-md border bg-muted/30 p-3 text-sm">
            <div className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
              Your answer
            </div>
            {a.answer_text.trim() ? (
              <Markdown>{a.answer_text}</Markdown>
            ) : (
              <span className="text-muted-foreground">No answer.</span>
            )}
          </div>
          {q.rubric && q.rubric.length > 0 && (
            <table className="mt-3 w-full text-sm">
              <tbody className="divide-y">
                {q.rubric.map((r, i) => {
                  const got = a.rubric_scores.find((x) => x.item === i)
                  return (
                    <tr key={i}>
                      <td className="py-1.5 pr-3">
                        {r.criterion}
                        {got?.comment && <div className="text-xs text-muted-foreground">{got.comment}</div>}
                      </td>
                      <td className="py-1.5 text-right whitespace-nowrap tabular-nums">
                        {got ? got.points : '–'} / {r.points}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </>
      )}

      {a.feedback && (
        <div className="mt-3 text-sm">
          <Markdown>{a.feedback}</Markdown>
        </div>
      )}
      {(q.answer || q.explanation) && (
        <details className="mt-3 text-sm">
          <summary className="cursor-pointer text-muted-foreground select-none">Model answer</summary>
          <div className="mt-2 space-y-2">
            {q.answer && <Markdown>{q.answer}</Markdown>}
            {q.explanation && (
              <div className="text-muted-foreground">
                <Markdown>{q.explanation}</Markdown>
              </div>
            )}
          </div>
        </details>
      )}
      <SourceLinks refs={q.source_refs ?? []} />
    </li>
  )
}

function SourceLinks({ refs }: { refs: NonNullable<ExamAnswer['question']['source_refs']> }) {
  const lecture = refs.filter((r) => r.lecture_id && r.start_s != null).slice(0, 3)
  if (!lecture.length) return null
  return (
    <div className="mt-2 flex flex-wrap gap-2 text-xs">
      <span className="text-muted-foreground">From the lecture:</span>
      {lecture.map((r, i) => (
        <Link
          key={i}
          to={`/lectures/${r.lecture_id}?t=${Math.floor(r.start_s!)}`}
          className="rounded bg-primary/10 px-1.5 py-px font-mono text-primary hover:bg-primary/20"
        >
          {r.page ? `p. ${r.page}` : formatDuration(r.start_s!)}
        </Link>
      ))}
    </div>
  )
}
