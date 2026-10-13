import { useQuery } from '@tanstack/react-query'
import { CalendarCheck } from 'lucide-react'
import { Link } from 'react-router'

import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import { PlanRow } from '@/pages/PlanPage'

/** Dashboard: today's study plan and deadlines due within a week. */
export function PlanTodayCard() {
  const { data: plan } = useQuery({ queryKey: ['plan', 1], queryFn: () => api.plan(1) })
  const { data: dl } = useQuery({ queryKey: ['deadlines'], queryFn: api.deadlines })
  const today = plan?.days[0]
  const week = new Date()
  week.setDate(week.getDate() + 7)
  const soon = (dl?.open ?? []).filter((d) => d.due_date && new Date(d.due_date) <= week)
  if (!today?.items.length && !soon.length && !dl?.suggested.length) return null

  return (
    <section className="mt-3 rounded-lg border bg-card p-4">
      <div className="flex items-center gap-2">
        <h2 className="flex items-center gap-2 text-sm font-medium">
          <CalendarCheck className="size-4" /> Today&apos;s plan
        </h2>
        {today && today.minutes > 0 && (
          <span className="text-xs text-muted-foreground tabular-nums">
            {today.done_minutes} / {today.minutes} min
          </span>
        )}
        <Button size="xs" variant="ghost" className="ml-auto" asChild>
          <Link to="/plan">Open plan</Link>
        </Button>
      </div>
      {today?.items.length ? (
        <ul className="mt-2 space-y-1">
          {today.items.map((i) => (
            <PlanRow key={i.id} item={i} />
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-sm text-muted-foreground">Nothing planned today.</p>
      )}
      {(soon.length > 0 || (dl?.suggested.length ?? 0) > 0) && (
        <div className="mt-3 border-t pt-2 text-xs text-muted-foreground">
          {soon.length > 0 && (
            <span>
              Due this week:{' '}
              {soon
                .slice(0, 3)
                .map((d) => d.title)
                .join(' · ')}
              {soon.length > 3 && ` +${soon.length - 3}`}
            </span>
          )}
          {(dl?.suggested.length ?? 0) > 0 && (
            <Link to="/plan" className="ml-2 text-amber-600 hover:underline dark:text-amber-400">
              {dl!.suggested.length} announcement{dl!.suggested.length === 1 ? '' : 's'} to review
            </Link>
          )}
        </div>
      )}
    </section>
  )
}
