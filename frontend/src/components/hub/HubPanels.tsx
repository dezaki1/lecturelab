import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FileText, FileUp, GraduationCap, Loader2, RefreshCw, Search, Star, Trash2 } from 'lucide-react'
import { useMemo, useRef, useState } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'

import { Markdown } from '@/components/Markdown'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { api, uploadSubjectDocument } from '@/lib/api'
import type { GlossaryEntry, Hub, HubTopic, PastExamSummary, SubjectDocument } from '@/lib/types'
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
  const [kind, setKind] = useState<'other' | 'past_exam'>('other')
  const { data: docs = [] } = useQuery({
    queryKey: ['subject-documents', subjectId],
    queryFn: () => api.subjectDocuments(subjectId),
    refetchInterval: (q) => (q.state.data?.some((d) => d.processing || d.analysing) ? 2000 : false),
  })
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['subject-documents', subjectId] })
    qc.invalidateQueries({ queryKey: ['exam-options', subjectId] })
  }
  const upload = useMutation({
    mutationFn: (f: File) => uploadSubjectDocument(subjectId, f, kind),
    onSuccess: refresh,
    onError: (e: Error) => toast.error(e.message),
  })
  const remove = useMutation({ mutationFn: api.deleteDocument, onSuccess: refresh })
  const analyse = useMutation({
    mutationFn: api.analyseDocument,
    onSuccess: refresh,
    onError: (e: Error) => toast.error(e.message),
  })
  const pick = (k: 'other' | 'past_exam') => {
    setKind(k)
    input.current?.click()
  }
  const status = (d: SubjectDocument) => {
    if (d.processing) return 'reading…'
    if (d.analysing) return 'analysing format and style…'
    if (d.lecture_id) return `L${d.lecture_number} · ${d.placed} of ${d.pages} pages placed`
    return `${d.pages} page${d.pages === 1 ? '' : 's'}`
  }
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
        <span className="min-w-60 flex-1">
          PDFs of this subject. Slide decks are best added on the lecture they belong to (Slides tab), so
          their pages are matched to the recording. Past exams (scans work too) teach the question writer and
          the exam simulator the real exam&apos;s format and style.
        </span>
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
        <Button size="sm" variant="outline" className="shrink-0" onClick={() => pick('past_exam')}>
          {upload.isPending && kind === 'past_exam' ? (
            <Loader2 className="animate-spin" />
          ) : (
            <GraduationCap />
          )}{' '}
          Add past exam
        </Button>
        <Button size="sm" variant="outline" className="shrink-0" onClick={() => pick('other')}>
          {upload.isPending && kind === 'other' ? <Loader2 className="animate-spin" /> : <FileUp />} Add PDF
        </Button>
      </div>
      {docs.length === 0 ? (
        <p className="py-6 text-sm text-muted-foreground">No PDFs yet.</p>
      ) : (
        <ul className="divide-y rounded-lg border bg-card text-sm">
          {docs.map((d) => (
            <li key={d.id} className="px-4 py-2">
              <div className="flex items-center gap-2">
                {d.kind === 'past_exam' ? (
                  <GraduationCap className="size-4 shrink-0 text-primary" />
                ) : (
                  <FileText className="size-4 shrink-0 text-muted-foreground" />
                )}
                <a
                  href={api.documentFileUrl(d.id)}
                  target="_blank"
                  rel="noreferrer"
                  className="truncate hover:underline"
                >
                  {d.title}
                </a>
                <span className="shrink-0 text-xs text-muted-foreground">{status(d)}</span>
                {d.kind === 'past_exam' && !d.past_exam && !d.processing && !d.analysing && (
                  <Button
                    size="xs"
                    variant="ghost"
                    disabled={analyse.isPending}
                    onClick={() => analyse.mutate(d.id)}
                  >
                    <RefreshCw /> Analyse
                  </Button>
                )}
                {d.lecture_id ? (
                  <Link
                    to={`/lectures/${d.lecture_id}`}
                    className="ml-auto text-xs text-primary hover:underline"
                  >
                    open lecture
                  </Link>
                ) : (
                  <Button
                    size="icon-xs"
                    variant="ghost"
                    className="ml-auto"
                    aria-label={`Remove ${d.title}`}
                    onClick={() => remove.mutate(d.id)}
                  >
                    <Trash2 />
                  </Button>
                )}
              </div>
              {d.past_exam && <PastExamCard exam={d.past_exam} />}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function PastExamCard({ exam: e }: { exam: PastExamSummary }) {
  const facts = [
    e.date,
    e.duration_min ? `${e.duration_min} min` : '',
    e.total_points ? `${e.total_points} points` : '',
    `${e.questions} task${e.questions === 1 ? '' : 's'}`,
    e.aids ? `aids: ${e.aids}` : '',
    e.ocr ? 'read from a scan' : '',
  ].filter(Boolean)
  const total = e.topics.reduce((s, t) => s + t.points, 0)
  return (
    <div className="mt-2 ml-6 space-y-2 rounded-md bg-muted/40 px-3 py-2.5">
      <div className="font-medium">
        {e.title} <span className="font-normal text-muted-foreground">· {facts.join(' · ')}</span>
      </div>
      {e.format && <p className="text-muted-foreground">{e.format}</p>}
      {e.style.length > 0 && (
        <ul className="list-disc space-y-0.5 pl-5 text-muted-foreground">
          {e.style.map((x, i) => (
            <li key={i}>{x}</li>
          ))}
        </ul>
      )}
      {e.topics.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {e.topics.map((t) => (
            <span key={t.name} className="rounded-full border bg-card px-2 py-0.5 text-xs">
              {t.name}
              {total > 0 && (
                <span className="text-muted-foreground"> · {Math.round((t.points / total) * 100)}%</span>
              )}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
