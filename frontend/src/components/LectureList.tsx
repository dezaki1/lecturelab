import { useQuery } from '@tanstack/react-query'
import { Bookmark, ChevronRight } from 'lucide-react'
import { Link } from 'react-router'

import { LectureStatusBadge } from '@/components/LectureStatusBadge'
import { api } from '@/lib/api'
import { formatDateTime, formatDuration } from '@/lib/dates'

export function LectureList({ subjectId }: { subjectId: number }) {
  const { data: lectures, isLoading } = useQuery({
    queryKey: ['lectures', subjectId],
    queryFn: () => api.lectures(subjectId),
  })

  if (isLoading) return null
  if (!lectures?.length)
    return (
      <p className="mt-2 text-sm text-muted-foreground">
        No lectures yet. Pick this subject in the recorder (top left) and press Record, or drop a recording
        anywhere on this page to import it.
      </p>
    )

  return (
    <ul className="mt-3 divide-y rounded-lg border bg-card">
      {lectures.map((l) => (
        <li key={l.id}>
          <Link
            to={`/lectures/${l.id}`}
            className="flex items-center gap-4 px-4 py-3 transition hover:bg-muted/50"
          >
            <span className="w-8 shrink-0 font-mono text-xs text-muted-foreground">L{l.number}</span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="truncate font-medium">{l.title}</span>
                {l.kind && <span className="text-xs text-muted-foreground">{l.kind}</span>}
              </div>
              <div className="mt-0.5 text-xs text-muted-foreground">
                {formatDateTime(l.started_at)}
                {l.source === 'document' ? (
                  <> · PDF{l.pages ? `, ${l.pages} pages` : ''}</>
                ) : (
                  l.duration_s != null && <> · {formatDuration(l.duration_s)}</>
                )}
              </div>
            </div>
            <LectureStatusBadge lecture={l} />
            {l.bookmark_count > 0 && (
              <span className="flex items-center gap-1 text-xs text-muted-foreground">
                <Bookmark className="size-3.5" /> {l.bookmark_count}
              </span>
            )}
            <ChevronRight className="size-4 text-muted-foreground" />
          </Link>
        </li>
      ))}
    </ul>
  )
}
