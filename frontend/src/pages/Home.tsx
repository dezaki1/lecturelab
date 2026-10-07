import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'

import { api } from '@/lib/api'
import { daysUntil } from '@/lib/dates'

export function Home() {
  const { data: subjects = [] } = useQuery({ queryKey: ['subjects'], queryFn: api.subjects })
  const { data: system } = useQuery({ queryKey: ['system'], queryFn: api.system })

  return (
    <div className="mx-auto max-w-5xl px-8 py-10">
      <h1 className="text-2xl font-semibold tracking-tight">Overview</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        {subjects.length} subjects
        {system && (
          <>
            {' '}
            · data in {system.data_dir} · {system.free_gb} GB free
          </>
        )}
      </p>

      <div className="mt-8 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {subjects.map((s) => {
          const days = s.exam_date ? daysUntil(s.exam_date) : null
          return (
            <Link
              key={s.id}
              to={`/subjects/${s.id}`}
              className="group rounded-lg border bg-card p-4 transition hover:border-foreground/20 hover:shadow-sm"
            >
              <div className="flex items-start gap-3">
                <span className="mt-1.5 size-2.5 shrink-0 rounded-full" style={{ background: s.color }} />
                <div className="min-w-0">
                  <div className="font-medium leading-snug">{s.name}</div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    {s.lecture_count} lecture{s.lecture_count === 1 ? '' : 's'}
                    {days !== null && days >= 0 && <> · exam in {days} days</>}
                  </div>
                </div>
              </div>
            </Link>
          )
        })}
      </div>
    </div>
  )
}
