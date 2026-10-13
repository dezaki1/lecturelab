import { useMutation, useQuery } from '@tanstack/react-query'
import { ChevronDown, Loader2, Star, Target } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router'
import { toast } from 'sonner'

import { Markdown } from '@/components/Markdown'
import { cloze } from '@/components/materials/ItemLists'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import type { WeakTopic } from '@/lib/types'
import { cn } from '@/lib/utils'

const pct = (x: number | null | undefined) => (x == null ? '—' : `${Math.round(x * 100)}%`)
const STATUS: Record<WeakTopic['status'], string> = {
  weak: 'bg-red-500/10 text-red-700 dark:text-red-300',
  ok: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300',
  untested: 'bg-muted text-muted-foreground',
}

/** Subject tab: topic strength from reviews and answers, and targeted practice. */
export function WeakSpotsPanel({ subjectId }: { subjectId: number }) {
  const navigate = useNavigate()
  const { data } = useQuery({ queryKey: ['weak-spots', subjectId], queryFn: () => api.weakSpots(subjectId) })
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const topics = data?.topics ?? []
  // Preselect the weak topics (at most three) once they are known.
  useEffect(() => {
    setPicked(
      new Set(
        topics
          .filter((t) => t.status === 'weak')
          .slice(0, 3)
          .map((t) => t.name),
      ),
    )
  }, [data])

  const practise = useMutation({
    mutationFn: () => api.createPracticeSet(subjectId, { topics: [...picked], n: 6 }),
    onSuccess: (ps) => navigate(`/practice?set=${ps.id}`),
    onError: (e: Error) => toast.error(e.message),
  })
  if (!data) return null
  if (!topics.length)
    return (
      <p className="py-6 text-sm text-muted-foreground">
        No topics yet: generate study material for a lecture.
      </p>
    )
  const toggle = (name: string) =>
    setPicked((p) => {
      const n = new Set(p)
      if (n.has(name)) n.delete(name)
      else if (n.size < 4) n.add(name)
      return n
    })
  const weak = topics.filter((t) => t.status === 'weak').length

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
        <span>
          {weak ? `${weak} weak topic${weak === 1 ? '' : 's'}` : 'No weak topics right now'} · based on your
          card reviews, practice and exams in the last {data.window_days} days
        </span>
        <Button
          size="sm"
          className="ml-auto"
          disabled={!picked.size || practise.isPending}
          onClick={() => practise.mutate()}
          title="Writes new questions aimed at what you got wrong in the selected topics"
        >
          {practise.isPending ? <Loader2 className="animate-spin" /> : <Target />} Practice{' '}
          {picked.size ? `${picked.size} topic${picked.size === 1 ? '' : 's'}` : 'my weak topics'}
        </Button>
      </div>
      <ul className="divide-y rounded-lg border bg-card">
        {topics.map((t) => (
          <TopicRow key={t.name} t={t} picked={picked.has(t.name)} onPick={() => toggle(t.name)} />
        ))}
      </ul>
    </div>
  )
}

function TopicRow({ t, picked, onPick }: { t: WeakTopic; picked: boolean; onPick: () => void }) {
  const [open, setOpen] = useState(false)
  const detail = t.forgotten_cards.length > 0 || t.wrong_answers.length > 0
  return (
    <li className="px-4 py-3">
      <div className="flex items-start gap-3">
        <input
          type="checkbox"
          className="mt-1 size-4 accent-primary"
          checked={picked}
          onChange={onPick}
          aria-label={`Practise ${t.name}`}
        />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className={cn('rounded px-1.5 py-px text-xs font-medium capitalize', STATUS[t.status])}>
              {t.status}
            </span>
            {t.exam_relevant && (
              <Star className="size-3.5 fill-amber-400 text-amber-500" aria-label="Exam relevant" />
            )}
            <span className="font-medium">{t.name}</span>
          </div>
          <div className="mt-1 flex flex-wrap gap-x-3 text-xs text-muted-foreground">
            {t.reviews > 0 && (
              <span>
                cards forgotten {t.forgotten} of {t.reviews} reviews
              </span>
            )}
            {t.answers > 0 && (
              <span>
                answers {t.score}/{t.max} points ({pct(t.max ? t.score / t.max : null)})
              </span>
            )}
            {t.status === 'untested' && <span>not practised yet</span>}
            {t.places.slice(0, 3).map((p) => (
              <Link
                key={`${p.lecture_id}-${p.start_s}`}
                to={`/lectures/${p.lecture_id}${p.start_s ? `?t=${Math.floor(p.start_s)}` : ''}`}
                className="text-primary hover:underline"
              >
                L{p.number}
              </Link>
            ))}
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <div
            className="flex items-center gap-2"
            title="Strength: recall, card memory and answer scores combined"
          >
            <div className="h-1.5 w-24 overflow-hidden rounded-full bg-muted">
              <div
                className={cn(
                  'h-full rounded-full',
                  t.strength == null
                    ? ''
                    : t.strength < 0.6
                      ? 'bg-red-400'
                      : t.strength < 0.8
                        ? 'bg-amber-400'
                        : 'bg-emerald-500',
                )}
                style={{ width: pct(t.strength) === '—' ? 0 : pct(t.strength) }}
              />
            </div>
            <span className="w-9 text-right text-xs tabular-nums">{pct(t.strength)}</span>
          </div>
          {detail && (
            <button
              type="button"
              className="flex items-center gap-0.5 text-xs text-muted-foreground hover:text-foreground"
              onClick={() => setOpen(!open)}
            >
              What went wrong <ChevronDown className={cn('size-3 transition', open && 'rotate-180')} />
            </button>
          )}
        </div>
      </div>
      {open && (
        <div className="mt-3 ml-7 space-y-3 text-sm">
          {t.forgotten_cards.length > 0 && (
            <div>
              <div className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                Cards you keep forgetting
              </div>
              <ul className="space-y-1">
                {t.forgotten_cards.map((c) => (
                  <li key={c.id} className="rounded-md border bg-muted/30 px-3 py-1.5">
                    <Markdown>{cloze(c.front, true)}</Markdown>
                    <span className="text-xs text-muted-foreground">forgotten {c.times}×</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {t.wrong_answers.length > 0 && (
            <div>
              <div className="mb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
                Answers that lost points
              </div>
              <ul className="space-y-1">
                {t.wrong_answers.map((w) => (
                  <li key={w.question_id} className="rounded-md border bg-muted/30 px-3 py-1.5">
                    <Markdown>{w.prompt}</Markdown>
                    <div className="text-xs text-muted-foreground">
                      {w.score}/{w.max} points{w.feedback ? ` · ${w.feedback.slice(0, 160)}` : ''}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </li>
  )
}

/** Dashboard card: the weakest topics across subjects. */
export function WeakSpotsCard() {
  const { data = [] } = useQuery({ queryKey: ['weak-overview'], queryFn: api.weakOverview })
  if (!data.length) return null
  return (
    <section className="mt-3 rounded-lg border bg-card p-4">
      <h2 className="flex items-center gap-1.5 text-sm font-medium">
        <Target className="size-4 text-red-500" /> Weak spots
      </h2>
      <ul className="mt-2 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
        {data.map((w) => (
          <li key={`${w.subject_id}-${w.name}`} className="flex items-center gap-2">
            <span className="size-2 shrink-0 rounded-full" style={{ background: w.color }} />
            <Link
              to={`/subjects/${w.subject_id}?tab=weak`}
              className="min-w-0 flex-1 truncate hover:underline"
            >
              {w.name}
            </Link>
            {w.exam_relevant && <Star className="size-3 fill-amber-400 text-amber-500" />}
            <span className="text-xs text-red-600 tabular-nums dark:text-red-400">{pct(w.strength)}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}
