import { useQuery } from '@tanstack/react-query'
import { FileText, Mic, PenLine, Presentation, Search } from 'lucide-react'
import { Link, useSearchParams } from 'react-router'

import { formatDuration } from '@/lib/dates'
import { api } from '@/lib/api'

/** "<<word>>" markers from the FTS snippet -> <mark>. */
function Snippet({ text }: { text: string }) {
  const parts = text.split(/<<|>>/)
  return (
    <p className="text-sm leading-relaxed text-muted-foreground">
      {parts.map((p, i) =>
        i % 2 ? (
          <mark key={i} className="rounded bg-amber-300/60 px-0.5 text-foreground dark:bg-amber-500/30">
            {p}
          </mark>
        ) : (
          p
        ),
      )}
    </p>
  )
}

export function SearchPage() {
  const [params] = useSearchParams()
  const q = params.get('q') ?? ''
  const { data: hits, isFetching } = useQuery({
    queryKey: ['search', q],
    queryFn: () => api.search(q),
    enabled: q.trim().length > 1,
  })

  return (
    <div className="mx-auto max-w-3xl px-4 sm:px-8 py-10">
      <h1 className="flex items-center gap-2 text-xl font-semibold">
        <Search className="size-5" /> Results for “{q}”
      </h1>
      {isFetching && <p className="mt-4 text-sm text-muted-foreground">Searching…</p>}
      {hits && hits.length === 0 && <p className="mt-4 text-sm text-muted-foreground">Nothing found.</p>}
      <ul className="mt-6 space-y-3">
        {hits?.map((h) => (
          <li key={h.chunk_id}>
            <Link
              to={`/lectures/${h.lecture_id}${
                h.source === 'handwritten'
                  ? '?tab=mynotes'
                  : h.start_s != null
                    ? `?t=${Math.floor(h.start_s)}`
                    : ''
              }`}
              className="block rounded-lg border bg-card p-4 transition hover:border-foreground/20"
            >
              <div className="mb-1 flex items-center gap-2 text-xs text-muted-foreground">
                {h.source === 'note' ? (
                  <FileText className="size-3.5" />
                ) : h.source === 'slide' ? (
                  <Presentation className="size-3.5" />
                ) : h.source === 'handwritten' ? (
                  <PenLine className="size-3.5" />
                ) : (
                  <Mic className="size-3.5" />
                )}
                <span className="font-medium text-foreground">{h.subject_name}</span>·
                <span>
                  L{h.lecture_number} {h.lecture_title}
                </span>
                {h.start_s != null && (
                  <span className="ml-auto font-mono text-primary">
                    {h.page != null ? `p. ${h.page}` : formatDuration(h.start_s)}
                  </span>
                )}
              </div>
              {h.heading && <div className="mb-1 text-sm font-medium">{h.heading}</div>}
              <Snippet text={h.snippet} />
            </Link>
          </li>
        ))}
      </ul>
    </div>
  )
}
