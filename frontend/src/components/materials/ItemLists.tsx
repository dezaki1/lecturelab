import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Check, ChevronRight, Eye, Lightbulb, Star, Trash2, X } from 'lucide-react'
import { useState } from 'react'

import { Markdown } from '@/components/Markdown'
import { FlaggedBadge, RefChips, RegenerateButton } from '@/components/materials/common'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import type { Card, QuestionItem } from '@/lib/types'
import { cn } from '@/lib/utils'

/** "{{c1::answer}}" -> "[…]" (hidden) or "**answer**" (revealed). */
function cloze(text: string, reveal: boolean) {
  return text.replace(/\{\{c\d+::(.+?)(?:::(.+?))?\}\}/g, (_, ans: string, hint?: string) =>
    reveal ? `**${ans}**` : `[${hint ?? '…'}]`.replace('[', '\\[').replace(']', '\\]'),
  )
}

function Header({
  lectureId,
  kind,
  count,
  noun,
}: {
  lectureId: number
  kind: string
  count: number
  noun: string
}) {
  return (
    <div className="mb-3 flex items-center text-sm text-muted-foreground">
      <span>
        {count} {noun}
        {count === 1 ? '' : 's'}
      </span>
      <span className="ml-auto">
        <RegenerateButton lectureId={lectureId} kind={kind} label={count ? 'Regenerate' : 'Generate'} />
      </span>
    </div>
  )
}

function useDelete(lectureId: number, fn: (id: number) => Promise<void>) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['materials', lectureId] }),
  })
}

export function FlashcardList({ lectureId, cards }: { lectureId: number; cards: Card[] }) {
  const del = useDelete(lectureId, api.deleteCard)
  return (
    <div>
      <Header lectureId={lectureId} kind="flashcards" count={cards.length} noun="card" />
      <div className="grid gap-3 md:grid-cols-2">
        {cards.map((c) => (
          <FlashcardTile key={c.id} card={c} onDelete={() => del.mutate(c.id)} />
        ))}
      </div>
    </div>
  )
}

function FlashcardTile({ card, onDelete }: { card: Card; onDelete: () => void }) {
  const [flipped, setFlipped] = useState(false)
  return (
    <div
      onClick={() => setFlipped(!flipped)}
      className="group relative flex min-h-32 cursor-pointer flex-col rounded-lg border bg-card p-4 transition hover:border-foreground/20"
    >
      <div className="mb-2 flex items-center gap-1.5 text-xs text-muted-foreground">
        <Badge variant="outline" className="capitalize">
          {card.type}
        </Badge>
        <span className="capitalize">{card.difficulty}</span>
        {card.tags.map((t) => (
          <span key={t}>#{t}</span>
        ))}
        {card.verified === 'flagged' && <FlaggedBadge note={card.verify_note} />}
        <button
          className="ml-auto opacity-0 transition group-hover:opacity-100"
          aria-label="Delete card"
          onClick={(e) => {
            e.stopPropagation()
            onDelete()
          }}
        >
          <Trash2 className="size-3.5" />
        </button>
      </div>
      <Markdown className="text-sm">
        {card.type === 'cloze' ? cloze(card.front, flipped) : card.front}
      </Markdown>
      {flipped && card.type !== 'cloze' && (
        <div className="mt-3 border-t pt-3">
          <Markdown className="text-sm">{card.back}</Markdown>
        </div>
      )}
      {flipped && card.type === 'cloze' && card.back && (
        <div className="mt-2 text-sm text-muted-foreground">
          <Markdown>{card.back}</Markdown>
        </div>
      )}
      <div className="mt-auto flex items-center pt-2 text-xs text-muted-foreground">
        {!flipped && <span>Click to reveal</span>}
        <span className="ml-auto" onClick={(e) => e.stopPropagation()}>
          <RefChips refs={card.source_refs} />
        </span>
      </div>
    </div>
  )
}

export function ExerciseList({ lectureId, items }: { lectureId: number; items: QuestionItem[] }) {
  const del = useDelete(lectureId, api.deleteQuestion)
  return (
    <div>
      <Header lectureId={lectureId} kind="exercises" count={items.length} noun="exercise" />
      <div className="space-y-4">
        {items.map((e, i) => (
          <Exercise key={e.id} n={i + 1} item={e} onDelete={() => del.mutate(e.id)} />
        ))}
      </div>
    </div>
  )
}

function Exercise({ n, item, onDelete }: { n: number; item: QuestionItem; onDelete: () => void }) {
  const [hints, setHints] = useState(0)
  const [solution, setSolution] = useState(false)
  return (
    <article className="group rounded-lg border bg-card p-5">
      <header className="mb-2 flex items-center gap-2">
        <h3 className="font-semibold">
          {n}. {item.title}
        </h3>
        <Badge variant="outline" className="capitalize">
          {item.type}
        </Badge>
        <span className="text-xs capitalize text-muted-foreground">{item.difficulty}</span>
        {item.verified === 'flagged' && <FlaggedBadge note={item.verify_note} />}
        <span className="ml-auto flex items-center">
          <RefChips refs={item.source_refs} />
          <button className="ml-2 opacity-0 group-hover:opacity-100" aria-label="Delete" onClick={onDelete}>
            <Trash2 className="size-3.5 text-muted-foreground" />
          </button>
        </span>
      </header>
      <Markdown>{item.prompt}</Markdown>
      {item.hints.slice(0, hints).map((h, i) => (
        <div key={i} className="mt-3 flex gap-2 rounded-md bg-muted/60 px-3 py-2 text-sm">
          <Lightbulb className="mt-0.5 size-4 shrink-0 text-amber-500" />
          <Markdown>{h}</Markdown>
        </div>
      ))}
      <div className="mt-3 flex gap-2">
        {hints < item.hints.length && !solution && (
          <Button size="xs" variant="outline" onClick={() => setHints(hints + 1)}>
            <Lightbulb /> Hint {hints + 1}/{item.hints.length}
          </Button>
        )}
        <Button size="xs" variant="outline" onClick={() => setSolution(!solution)}>
          <Eye /> {solution ? 'Hide solution' : 'Show solution'}
        </Button>
      </div>
      {solution && (
        <div className="mt-4 space-y-2 border-t pt-4">
          <ol className="space-y-2">
            {item.solution_steps.map((s, i) => (
              <li key={i} className="flex gap-2">
                <span className="w-5 shrink-0 text-right text-sm text-muted-foreground">{i + 1}.</span>
                <Markdown className="flex-1">{s}</Markdown>
              </li>
            ))}
          </ol>
          <div className="rounded-md bg-emerald-500/10 px-3 py-2 text-sm">
            <span className="font-medium">Answer: </span>
            <Markdown className="inline [&>*]:inline">{item.answer}</Markdown>
          </div>
          {item.expected_output && (
            <pre className="rounded-md bg-muted p-3 text-xs">{item.expected_output}</pre>
          )}
        </div>
      )}
    </article>
  )
}

const TYPE_LABEL: Record<string, string> = {
  mcq: 'Multiple choice',
  multi: 'Multiple select',
  tf: 'True/false',
  short: 'Short answer',
  long: 'Long answer',
  calculation: 'Calculation',
  code: 'Code',
}

export function QuestionList({ lectureId, items }: { lectureId: number; items: QuestionItem[] }) {
  const del = useDelete(lectureId, api.deleteQuestion)
  const total = items.reduce((s, q) => s + q.points, 0)
  return (
    <div>
      <Header lectureId={lectureId} kind="questions" count={items.length} noun="question" />
      {items.length > 0 && (
        <p className="-mt-2 mb-3 text-xs text-muted-foreground">{total} points in total</p>
      )}
      <div className="space-y-4">
        {items.map((q, i) => (
          <QuestionCard key={q.id} n={i + 1} q={q} onDelete={() => del.mutate(q.id)} />
        ))}
      </div>
    </div>
  )
}

function QuestionCard({ n, q, onDelete }: { n: number; q: QuestionItem; onDelete: () => void }) {
  const [reveal, setReveal] = useState(false)
  const [picked, setPicked] = useState<number[]>([])
  const choice = q.options.length > 0
  const toggle = (i: number) =>
    setPicked((p) => (q.type === 'multi' ? (p.includes(i) ? p.filter((x) => x !== i) : [...p, i]) : [i]))

  return (
    <article className="group rounded-lg border bg-card p-5">
      <header className="mb-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span className="text-sm font-semibold text-foreground">Q{n}</span>
        <Badge variant="outline">{TYPE_LABEL[q.type] ?? q.type}</Badge>
        <span className="capitalize">{q.bloom}</span>·<span className="capitalize">{q.difficulty}</span>·
        <span>
          {q.points} pt{q.points === 1 ? '' : 's'}
        </span>
        {q.emphasized && (
          <span className="flex items-center gap-0.5 text-amber-600 dark:text-amber-400">
            <Star className="size-3 fill-current" /> emphasised
          </span>
        )}
        {q.verified === 'flagged' && <FlaggedBadge note={q.verify_note} />}
        <span className="ml-auto flex items-center">
          <RefChips refs={q.source_refs} />
          <button className="ml-2 opacity-0 group-hover:opacity-100" aria-label="Delete" onClick={onDelete}>
            <Trash2 className="size-3.5" />
          </button>
        </span>
      </header>
      <Markdown>{q.prompt}</Markdown>

      {choice && (
        <ul className="mt-3 space-y-1.5">
          {q.options.map((o, i) => {
            const chosen = picked.includes(i)
            return (
              <li key={i}>
                <button
                  disabled={reveal}
                  onClick={() => toggle(i)}
                  className={cn(
                    'flex w-full items-start gap-2 rounded-md border px-3 py-2 text-left text-sm transition',
                    !reveal && (chosen ? 'border-primary bg-primary/5' : 'hover:bg-muted/50'),
                    reveal && o.correct && 'border-emerald-500/60 bg-emerald-500/10',
                    reveal && chosen && !o.correct && 'border-destructive/60 bg-destructive/10',
                  )}
                >
                  <span className="mt-0.5 w-4 shrink-0">
                    {reveal ? (
                      o.correct ? (
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
                    {reveal && (
                      <span className="mt-1 block text-xs text-muted-foreground">{o.explanation}</span>
                    )}
                  </span>
                </button>
              </li>
            )
          })}
        </ul>
      )}

      <div className="mt-3">
        <Button size="xs" variant="outline" onClick={() => setReveal(!reveal)}>
          <Eye /> {reveal ? 'Hide answer' : choice ? 'Check answer' : 'Show model answer'}
        </Button>
      </div>
      {reveal && (
        <div className="mt-3 space-y-3 border-t pt-3 text-sm">
          {!choice && (
            <div>
              <div className="mb-1 font-medium">Model answer</div>
              <Markdown>{q.answer}</Markdown>
            </div>
          )}
          {q.explanation && (
            <div className="text-muted-foreground">
              <Markdown>{q.explanation}</Markdown>
            </div>
          )}
          {q.rubric.length > 0 && (
            <div>
              <div className="mb-1 font-medium">Rubric</div>
              <ul className="space-y-0.5">
                {q.rubric.map((r, i) => (
                  <li key={i} className="flex gap-2">
                    <span className="w-10 shrink-0 text-right tabular-nums text-muted-foreground">
                      {r.points} pt
                    </span>
                    <span>{r.criterion}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </article>
  )
}
