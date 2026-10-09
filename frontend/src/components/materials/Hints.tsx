import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Eye, Lightbulb, Loader2, Plus, Repeat, Sparkles, Star } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'

import { Markdown, TimeChip } from '@/components/Markdown'
import { RefChips, RegenerateButton } from '@/components/materials/common'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import type { ExamHint, Explanation } from '@/lib/types'
import { cn } from '@/lib/utils'

const KIND: Record<string, { label: string; icon: typeof Star; className: string }> = {
  exam: { label: 'Exam', icon: Star, className: 'fill-amber-400 text-amber-500' },
  important: { label: 'Important', icon: Star, className: 'text-amber-500' },
  repeated: { label: 'Repeated', icon: Repeat, className: 'text-sky-500' },
}

export function HintIcon({ kind, className }: { kind: string; className?: string }) {
  const k = KIND[kind] ?? KIND.important
  return <k.icon className={cn('size-3.5 shrink-0', k.className, className)} />
}

/** The lecturer's exam hints for one lecture (tab on the lecture page). */
export function HintList({ lectureId, hints }: { lectureId: number; hints: ExamHint[] }) {
  const fromKeywords = hints.length > 0 && hints.every((h) => h.source === 'rule')
  return (
    <div>
      <div className="mb-3 flex items-center gap-2 text-sm text-muted-foreground">
        <span>
          {hints.length
            ? `${hints.length} moment${hints.length === 1 ? '' : 's'} the lecturer flagged`
            : 'The lecturer did not flag anything as exam-relevant in this lecture.'}
          {fromKeywords && ' (found by keyword search; generating study material checks them)'}
        </span>
        <span className="ml-auto">
          <RegenerateButton
            lectureId={lectureId}
            kind="hints"
            label={hints.length ? 'Find again' : 'Search'}
          />
        </span>
      </div>
      {hints.length > 0 && (
        <ul className="divide-y rounded-lg border bg-card">
          {hints.map((h) => (
            <li key={h.id} className="flex gap-3 px-4 py-3 text-sm">
              <HintIcon kind={h.kind} className="mt-1" />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <TimeChip t={h.start_s} />
                  <span className="font-medium">{h.topic || h.quote}</span>
                </div>
                {h.topic && h.quote && <p className="mt-1 text-muted-foreground italic">“{h.quote}”</p>}
              </div>
              <span className="shrink-0 text-xs text-muted-foreground">{KIND[h.kind]?.label ?? h.kind}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/** All exam hints of a subject, by lecture: an exam-prep checklist on the subject page. */
export function SubjectHints({ subjectId }: { subjectId: number }) {
  const { data: hints = [] } = useQuery({
    queryKey: ['subject-hints', subjectId],
    queryFn: () => api.subjectHints(subjectId),
  })
  if (!hints.length) return null
  const byLecture = new Map<number, typeof hints>()
  for (const h of hints) byLecture.set(h.lecture_id, [...(byLecture.get(h.lecture_id) ?? []), h])
  return (
    <section className="mt-8">
      <h2 className="flex items-center gap-2 text-lg font-semibold">
        <Star className="size-4 fill-amber-400 text-amber-500" /> Exam hints
        <span className="text-sm font-normal text-muted-foreground">{hints.length}</span>
      </h2>
      <p className="mt-1 text-sm text-muted-foreground">
        What the lecturer said is important, across all lectures. Click a time to hear it again.
      </p>
      <div className="mt-3 divide-y rounded-lg border bg-card">
        {[...byLecture.values()].map((group) => (
          <div key={group[0].lecture_id} className="px-4 py-3">
            <div className="mb-1 text-xs font-medium text-muted-foreground">
              L{group[0].lecture_number} {group[0].lecture_title}
            </div>
            <ul className="space-y-1">
              {group.map((h) => (
                <li key={h.id} className="flex items-start gap-2 text-sm">
                  <HintIcon kind={h.kind} className="mt-1" />
                  <Link
                    to={`/lectures/${h.lecture_id}?t=${Math.floor(h.start_s)}`}
                    className="shrink-0 font-mono text-xs text-primary hover:underline"
                  >
                    {formatTime(h.start_s)}
                  </Link>
                  <span>{h.topic || h.quote}</span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  )
}

function formatTime(t: number) {
  const m = Math.floor(t / 60)
  const s = Math.floor(t % 60)
  return m >= 60
    ? `${Math.floor(m / 60)}:${String(m % 60).padStart(2, '0')}:${String(s).padStart(2, '0')}`
    : `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

/** "Explain this part" under a confused/question bookmark. */
export function ExplanationPanel({
  lectureId,
  explanation,
  generating,
  canGenerate,
}: {
  lectureId: number
  explanation?: Explanation
  generating: boolean
  canGenerate: boolean
}) {
  const qc = useQueryClient()
  const [reveal, setReveal] = useState(false)
  const explain = useMutation({
    mutationFn: () => api.generate(lectureId, ['explanations']),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['lecture', lectureId] }),
    onError: (e: Error) => toast.error(e.message),
  })
  const toCard = useMutation({
    mutationFn: (id: number) => api.explanationToCard(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['materials', lectureId] })
      toast.success('Added as a flashcard')
    },
    onError: (e: Error) => toast.error(e.message),
  })

  if (!explanation) {
    return (
      <div className="mt-1 ml-7 flex items-center gap-2 text-xs text-muted-foreground">
        {generating ? (
          <>
            <Loader2 className="size-3.5 animate-spin" /> Writing an explanation…
          </>
        ) : (
          <Button
            size="xs"
            variant="outline"
            disabled={!canGenerate || explain.isPending}
            onClick={() => explain.mutate()}
          >
            <Sparkles /> Explain this part
          </Button>
        )}
      </div>
    )
  }
  return (
    <div className="mt-2 ml-7 rounded-md border bg-muted/30 p-3 text-sm">
      <div className="mb-1 flex items-center gap-2 font-medium">
        <Lightbulb className="size-4 text-amber-500" /> {explanation.title || 'Explain this part'}
        <RefChips refs={explanation.source_refs} />
      </div>
      <Markdown>{explanation.explanation_md}</Markdown>
      {explanation.example_md && (
        <div className="mt-2">
          <div className="text-xs font-medium tracking-wide text-muted-foreground uppercase">Example</div>
          <Markdown>{explanation.example_md}</Markdown>
        </div>
      )}
      {explanation.check_question && (
        <div className="mt-3 rounded-md border bg-background p-2.5">
          <div className="text-xs font-medium tracking-wide text-muted-foreground uppercase">
            Check yourself
          </div>
          <Markdown>{explanation.check_question}</Markdown>
          {reveal ? (
            <div className="mt-1 text-muted-foreground">
              <Markdown>{explanation.check_answer}</Markdown>
            </div>
          ) : (
            <Button size="xs" variant="ghost" className="mt-1" onClick={() => setReveal(true)}>
              <Eye /> Show answer
            </Button>
          )}
          <Button
            size="xs"
            variant="ghost"
            className="mt-1"
            disabled={toCard.isPending}
            onClick={() => toCard.mutate(explanation.id)}
          >
            <Plus /> Add as flashcard
          </Button>
        </div>
      )}
    </div>
  )
}
