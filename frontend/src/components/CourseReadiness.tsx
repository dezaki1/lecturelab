import { Link } from 'react-router'

import type { SubjectProgress } from '@/lib/types'
import { cn } from '@/lib/utils'

const pct = (x: number | null | undefined) => (x == null ? '—' : `${Math.round(x * 100)}%`)

/** What to do next for a course, from the biggest gap. */
function nextStep(s: SubjectProgress): string | null {
  const r = s.readiness
  if (s.due) return `${s.due} card${s.due === 1 ? '' : 's'} due for review`
  if (r.cards_unseen) return `${r.cards_unseen} card${r.cards_unseen === 1 ? '' : 's'} never studied`
  if (r.questions_unanswered)
    return `${r.questions_unanswered} exam question${r.questions_unanswered === 1 ? '' : 's'} not tried yet`
  return null
}

/** Overview card: one readiness bar per course (covered x remembered x practised), exams first. */
export function CourseReadiness({ subjects }: { subjects: SubjectProgress[] }) {
  const withMaterial = subjects.filter((s) => s.readiness.score != null)
  const empty = subjects.filter((s) => s.readiness.score == null)
  return (
    <section className="mt-3 rounded-lg border bg-card">
      <div className="flex flex-wrap items-baseline justify-between gap-2 px-4 pt-4">
        <h2 className="text-sm font-medium">Your courses</h2>
        <span className="text-xs text-muted-foreground">
          readiness = what you&apos;ve studied, still remember and can answer
        </span>
      </div>
      <ul className="mt-2 divide-y">
        {withMaterial.map((s) => (
          <CourseRow key={s.subject_id} s={s} />
        ))}
      </ul>
      {empty.length > 0 && (
        <p className="border-t px-4 py-3 text-xs text-muted-foreground">
          No cards or questions yet:{' '}
          {empty.map((s, i) => (
            <span key={s.subject_id}>
              {i > 0 && ', '}
              <Link to={`/subjects/${s.subject_id}`} className="hover:underline">
                {s.name}
              </Link>
            </span>
          ))}
        </p>
      )}
    </section>
  )
}

function CourseRow({ s }: { s: SubjectProgress }) {
  const r = s.readiness
  const step = nextStep(s)
  const items = s.cards + s.questions
  return (
    <li className="px-4 py-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Link
          to={`/subjects/${s.subject_id}`}
          className="flex min-w-0 flex-1 items-center gap-2 font-medium hover:underline"
        >
          <span className="size-2.5 shrink-0 rounded-full" style={{ background: s.color }} />
          <span className="truncate">{s.name}</span>
        </Link>
        {s.days_left != null && s.days_left >= 0 && (
          <span
            className={cn(
              'text-xs whitespace-nowrap text-muted-foreground',
              s.days_left <= 14 && 'font-medium text-amber-600 dark:text-amber-400',
            )}
          >
            exam in {s.days_left} day{s.days_left === 1 ? '' : 's'}
          </span>
        )}
      </div>
      {r.score != null && (
        <>
          <div className="mt-1.5 flex items-center gap-3">
            <div
              className="h-2.5 flex-1 overflow-hidden rounded-full bg-muted"
              role="progressbar"
              aria-label={`${s.name}: readiness`}
              aria-valuenow={Math.round(r.score * 100)}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <div
                className="h-full rounded-full transition-[width]"
                style={{ width: pct(r.score), background: s.color }}
              />
            </div>
            <span className="w-10 text-right text-sm font-semibold tabular-nums">{pct(r.score)}</span>
          </div>
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
            <span
              title={`${items - r.cards_unseen - r.questions_unanswered} of ${items} cards and questions`}
            >
              covered {pct(r.covered)}
            </span>
            <span title="How likely you are to recall the cards you've studied, today">
              remembered {pct(r.remembered)}
            </span>
            <span title="Your latest scores on the exam questions you've answered">
              practised {pct(r.practised)}
            </span>
            <span className="tabular-nums">
              · {s.lectures_ready} lecture{s.lectures_ready === 1 ? '' : 's'}
            </span>
            {step && (
              <span className="basis-full sm:basis-auto">
                ·{' '}
                {s.due ? (
                  <Link to={`/review?subject=${s.subject_id}`} className="text-primary hover:underline">
                    {step}
                  </Link>
                ) : (
                  step
                )}
              </span>
            )}
          </div>
        </>
      )}
    </li>
  )
}
