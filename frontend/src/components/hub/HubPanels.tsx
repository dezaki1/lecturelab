import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BookOpen, FileText, FileUp, Loader2, RefreshCw, Search, Star, Trash2 } from 'lucide-react'
import { useMemo, useRef, useState } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'

import { Markdown } from '@/components/Markdown'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { api, uploadSubjectDocument } from '@/lib/api'
import type { GlossaryEntry, Hub, HubTopic } from '@/lib/types'
import { cn } from '@/lib/utils'

const pct = (x: number | null) => (x == null ? '—' : `${Math.round(x * 100)}%`)
const when = (iso: string) =>
  new Date(iso).toLocaleString(undefined, {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })

/** "Updated …" line with the update button, shared by the overview, topics and map tabs. */
export function HubStatus({ subjectId, hub }: { subjectId: number; hub: Hub }) {
  const qc = useQueryClient()
  const update = useMutation({
    mutationFn: () => api.refreshHub(subjectId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['hub', subjectId] }),
    onError: (e: Error) => toast.error(e.message),
  })
  const busy = hub.updating || update.isPending
  return (
    <div className="mb-4 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
      {busy ? (
        <span className="flex items-center gap-1.5">
          <Loader2 className="size-3.5 animate-spin" /> Updating the course overview…
        </span>
      ) : hub.overview ? (
        <span>
          Covers {hub.overview.lectures_covered} lecture{hub.overview.lectures_covered === 1 ? '' : 's'} ·
          updated {when(hub.overview.created_at)}
          {hub.outdated && (
            <span className="ml-1 text-amber-600 dark:text-amber-400">· newer lectures not included yet</span>
          )}
        </span>
      ) : (
        <span>No course overview yet. It is built from the lectures&apos; study notes.</span>
      )}
      <Button
        size="xs"
        variant="outline"
        className="ml-auto"
        disabled={busy || !hub.can_update}
        onClick={() => update.mutate()}
      >
        <RefreshCw /> {hub.overview ? 'Update' : 'Build overview'}
      </Button>
    </div>
  )
}

export function OverviewPanel({ subjectId, hub }: { subjectId: number; hub: Hub }) {
  return (
    <div>
      <HubStatus subjectId={subjectId} hub={hub} />
      {hub.overview && <Markdown>{hub.overview.content_md}</Markdown>}
    </div>
  )
}

function MasteryBar({ value }: { value: number | null }) {
  return (
    <div className="flex items-center gap-2" title="How much of this topic's cards you would recall today">
      <div className="h-1.5 w-24 overflow-hidden rounded-full bg-muted">
        <div
          className={cn(
            'h-full rounded-full',
            value == null
              ? ''
              : value >= 0.8
                ? 'bg-emerald-500'
                : value >= 0.5
                  ? 'bg-amber-400'
                  : 'bg-red-400',
          )}
          style={{ width: pct(value) === '—' ? 0 : pct(value) }}
        />
      </div>
      <span className="w-9 text-right text-xs tabular-nums">{pct(value)}</span>
    </div>
  )
}

export function TopicsPanel({ subjectId, hub }: { subjectId: number; hub: Hub }) {
  const [sort, setSort] = useState<'course' | 'weakest'>('course')
  const topics = useMemo(() => {
    const t = [...hub.topics]
    if (sort === 'weakest') t.sort((a, b) => (a.mastery ?? -1) - (b.mastery ?? -1))
    return t
  }, [hub.topics, sort])
  return (
    <div>
      <HubStatus subjectId={subjectId} hub={hub} />
      {topics.length > 0 && (
        <>
          <div className="mb-2 flex gap-1 text-xs">
            {(['course', 'weakest'] as const).map((k) => (
              <Button
                key={k}
                size="xs"
                variant={sort === k ? 'secondary' : 'ghost'}
                onClick={() => setSort(k)}
              >
                {k === 'course' ? 'Course order' : 'Weakest first'}
              </Button>
            ))}
          </div>
          <ul className="divide-y rounded-lg border bg-card">
            {topics.map((t) => (
              <TopicRow key={t.name} topic={t} />
            ))}
          </ul>
        </>
      )}
    </div>
  )
}

function TopicRow({ topic: t }: { topic: HubTopic }) {
  return (
    <li className="flex flex-wrap items-start gap-x-4 gap-y-2 px-4 py-3">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5 font-medium">
          {t.exam_relevant && (
            <Star className="size-3.5 shrink-0 fill-amber-400 text-amber-500" aria-label="Exam relevant" />
          )}
          {t.name}
        </div>
        {t.summary && <p className="text-sm text-muted-foreground">{t.summary}</p>}
        <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-xs">
          {t.places.map((p) => (
            <Link
              key={`${p.lecture_id}-${p.start_s}`}
              to={`/lectures/${p.lecture_id}${p.start_s ? `?t=${Math.floor(p.start_s)}` : ''}`}
              className="text-primary hover:underline"
              title={p.sections.join(' · ')}
            >
              L{p.number} {p.title}
            </Link>
          ))}
        </div>
      </div>
      <div className="flex shrink-0 flex-col items-end gap-1 text-xs text-muted-foreground">
        <MasteryBar value={t.mastery} />
        <span>
          {t.cards} card{t.cards === 1 ? '' : 's'}
          {t.due > 0 && ` · ${t.due} due`}
          {t.new > 0 && ` · ${t.new} new`}
          {t.practice != null && ` · practice ${pct(t.practice)}`}
        </span>
      </div>
    </li>
  )
}

export function GlossaryPanel({ glossary }: { glossary: GlossaryEntry[] }) {
  const [q, setQ] = useState('')
  const shown = useMemo(() => {
    const s = q.trim().toLowerCase()
    if (!s) return glossary
    return glossary.filter((g) => [g.term, g.german, g.definition].some((x) => x.toLowerCase().includes(s)))
  }, [glossary, q])
  if (!glossary.length)
    return (
      <p className="py-6 text-sm text-muted-foreground">
        The glossary fills up as lectures get study material.
      </p>
    )
  return (
    <div>
      <div className="relative mb-3 max-w-sm">
        <Search className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={`Search ${glossary.length} terms (English or German)`}
          className="h-8 pl-8"
        />
      </div>
      <dl className="divide-y rounded-lg border bg-card">
        {shown.map((g) => (
          <div key={g.term} className="grid gap-1 px-4 py-2.5 sm:grid-cols-[14rem_1fr]">
            <dt>
              <div className="font-medium">{g.term}</div>
              {g.german && <div className="text-sm text-muted-foreground italic">{g.german}</div>}
            </dt>
            <dd className="text-sm">
              <Markdown>{g.definition}</Markdown>
              <div className="mt-0.5 flex flex-wrap gap-x-2 text-xs">
                {g.lectures.map((l) => (
                  <Link
                    key={l.lecture_id}
                    to={`/lectures/${l.lecture_id}${l.t_s != null ? `?t=${l.t_s}` : ''}`}
                    className="text-primary hover:underline"
                  >
                    L{l.number}
                  </Link>
                ))}
              </div>
            </dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

export function MaterialsPanel({ subjectId }: { subjectId: number }) {
  const qc = useQueryClient()
  const input = useRef<HTMLInputElement>(null)
  const { data: docs = [] } = useQuery({
    queryKey: ['subject-documents', subjectId],
    queryFn: () => api.subjectDocuments(subjectId),
    refetchInterval: (q) => (q.state.data?.some((d) => d.processing) ? 2000 : false),
  })
  const refresh = () => qc.invalidateQueries({ queryKey: ['subject-documents', subjectId] })
  const [study, setStudy] = useState(true)
  const upload = useMutation({
    mutationFn: (f: File) => uploadSubjectDocument(subjectId, f, 'other', study),
    onSuccess: () => {
      refresh()
      qc.invalidateQueries({ queryKey: ['lectures'] })
      if (study) toast.success('Reading the PDF; its notes, cards and questions follow in a few minutes')
    },
    onError: (e: Error) => toast.error(e.message),
  })
  const studyIt = useMutation({
    mutationFn: api.studyDocument,
    onSuccess: () => {
      refresh()
      qc.invalidateQueries({ queryKey: ['lectures'] })
      toast.success('Studying it: notes, cards and questions follow in a few minutes')
    },
    onError: (e: Error) => toast.error(e.message),
  })
  const remove = useMutation({ mutationFn: api.deleteDocument, onSuccess: refresh })
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-2 text-sm text-muted-foreground">
        <span className="min-w-64 flex-1">
          PDFs of this course: scripts, papers, exercise sheets. Studied like a lecture, a PDF gets its own
          notes, glossary, cards and exam questions (linked to its pages) and flows into the course overview,
          cheat sheet and chat. Slide decks of one lecture are best added on that lecture (Slides tab).
        </span>
        <label className="flex shrink-0 items-center gap-1.5 text-foreground">
          <input
            type="checkbox"
            className="size-4 accent-primary"
            checked={study}
            onChange={(e) => setStudy(e.target.checked)}
          />
          Study it like a lecture
        </label>
        <input
          ref={input}
          type="file"
          accept="application/pdf,.pdf"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) upload.mutate(f)
            e.target.value = ''
          }}
        />
        <Button size="sm" variant="outline" className="shrink-0" onClick={() => input.current?.click()}>
          {upload.isPending ? <Loader2 className="animate-spin" /> : <FileUp />} Add PDF
        </Button>
      </div>
      {docs.length === 0 ? (
        <p className="py-6 text-sm text-muted-foreground">No PDFs yet.</p>
      ) : (
        <ul className="divide-y rounded-lg border bg-card text-sm">
          {docs.map((d) => (
            <li key={d.id} className="flex items-center gap-2 px-4 py-2">
              <FileText className="size-4 shrink-0 text-muted-foreground" />
              <a
                href={api.documentFileUrl(d.id)}
                target="_blank"
                rel="noreferrer"
                className="truncate hover:underline"
              >
                {d.title}
              </a>
              <span className="shrink-0 text-xs text-muted-foreground">
                {d.processing
                  ? 'reading…'
                  : d.kind === 'study'
                    ? `L${d.lecture_number} · studied like a lecture, ${d.pages} pages`
                    : d.lecture_id
                      ? `L${d.lecture_number} · ${d.placed} of ${d.pages} pages placed`
                      : `${d.pages} pages, background only`}
              </span>
              {d.lecture_id ? (
                <Link
                  to={`/lectures/${d.lecture_id}`}
                  className="ml-auto text-xs text-primary hover:underline"
                >
                  open lecture
                </Link>
              ) : (
                <>
                  <Button
                    size="xs"
                    variant="outline"
                    className="ml-auto"
                    title="Make notes, cards and questions from it, like a lecture"
                    onClick={() => studyIt.mutate(d.id)}
                    disabled={studyIt.isPending || d.processing}
                  >
                    <BookOpen /> Study it
                  </Button>
                  <Button
                    size="icon-xs"
                    variant="ghost"
                    aria-label={`Remove ${d.title}`}
                    onClick={() => remove.mutate(d.id)}
                  >
                    <Trash2 />
                  </Button>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
