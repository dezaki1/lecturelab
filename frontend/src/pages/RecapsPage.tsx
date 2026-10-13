import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Headphones, Loader2, Plus } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'

import { RecapPlayer } from '@/components/RecapPlayer'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useEvent } from '@/hooks/useEvents'
import { api } from '@/lib/api'
import { formatDateTime } from '@/lib/dates'
import type { Recap } from '@/lib/types'

const LENGTHS = [5, 7, 10, 15]

function weekLabel(iso: string) {
  const [y, m, d] = iso.split('-').map(Number)
  const start = new Date(y, m - 1, d)
  const end = new Date(y, m - 1, d + 6)
  const f = (x: Date) => x.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
  return `${f(start)} – ${f(end)}`
}

export function RecapsPage() {
  const qc = useQueryClient()
  const { data: recaps = [] } = useQuery({ queryKey: ['recaps'], queryFn: () => api.recaps() })
  const { data: system } = useQuery({ queryKey: ['system'], queryFn: api.system })
  useEvent('recap', () => qc.invalidateQueries({ queryKey: ['recaps'] }))
  const remake = useMutation({
    mutationFn: (r: Recap) =>
      api.createRecap({
        scope: r.scope,
        lecture_id: r.lecture_id ?? undefined,
        subject_id: r.scope === 'week' ? (r.subject_id ?? undefined) : undefined,
        week_start: r.week_start ?? undefined,
        minutes: r.minutes,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['recaps'] }),
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <div className="mx-auto max-w-3xl px-4 py-10 sm:px-8">
      <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
        <Headphones className="size-6" /> Audio recaps
      </h1>
      <p className="mt-1 text-sm text-muted-foreground">
        Spoken summaries of a lecture or a whole week, to listen to on the way. Written by the AI from your
        notes and read by your local voice; chapters link back to the lecture.
      </p>

      <NewRecap />
      {!system?.remote && <AutoRecap />}

      <ul className="mt-6 space-y-3">
        {recaps.map((r) => (
          <li key={`${r.id}-${r.created_at}`} className="rounded-lg border bg-card p-4">
            <div className="mb-3 flex flex-wrap items-baseline gap-2">
              <Badge variant="outline">{r.scope === 'lecture' ? 'Lecture' : 'Week'}</Badge>
              {r.scope === 'lecture' && r.lecture_id ? (
                <Link to={`/lectures/${r.lecture_id}`} className="font-medium hover:underline">
                  {r.title}
                </Link>
              ) : (
                <span className="font-medium">{r.title}</span>
              )}
              {r.scope === 'week' && r.week_start && (
                <span className="text-xs text-muted-foreground">{weekLabel(r.week_start)}</span>
              )}
              <span className="ml-auto text-xs text-muted-foreground">{formatDateTime(r.created_at)}</span>
            </div>
            <RecapPlayer recap={r} onRemake={() => remake.mutate(r)} />
          </li>
        ))}
        {!recaps.length && (
          <li className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
            No recaps yet. Make one above, or from the Audio recap tab of a lecture.
          </li>
        )}
      </ul>
    </div>
  )
}

function NewRecap() {
  const qc = useQueryClient()
  const [scope, setScope] = useState<'lecture' | 'week'>('week')
  const [minutes, setMinutes] = useState('7')
  const { data: subjects = [] } = useQuery({ queryKey: ['subjects'], queryFn: api.subjects })
  const { data: weeks = [] } = useQuery({ queryKey: ['recap-weeks'], queryFn: api.recapWeeks })
  const [subject, setSubject] = useState<string | null>(null)
  const [week, setWeek] = useState<string | null>(null)
  const [lecture, setLecture] = useState<string | null>(null)
  const subjectId =
    subject ?? (scope === 'lecture' ? String(subjects.find((s) => s.lecture_count)?.id ?? '') : 'all')
  const weekStart = week ?? weeks[0]?.week_start ?? ''
  const { data: lectures = [] } = useQuery({
    queryKey: ['lectures', Number(subjectId)],
    queryFn: () => api.lectures(Number(subjectId)),
    enabled: scope === 'lecture' && Number(subjectId) > 0,
  })
  const lectureId = lecture ?? (lectures[0] ? String(lectures[0].id) : '')
  const weekSubjects = weeks.find((w) => w.week_start === weekStart)?.subjects ?? []

  const create = useMutation({
    mutationFn: () =>
      api.createRecap(
        scope === 'lecture'
          ? { scope, lecture_id: Number(lectureId), minutes: Number(minutes) }
          : {
              scope,
              week_start: weekStart,
              subject_id: subjectId === 'all' ? undefined : Number(subjectId),
              minutes: Number(minutes),
            },
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['recaps'] })
      toast.success('Recap queued: it appears below when it is ready')
    },
    onError: (e: Error) => toast.error(e.message),
  })
  const ready = scope === 'lecture' ? !!lectureId : !!weekStart

  return (
    <section className="mt-6 space-y-4 rounded-lg border bg-card p-5 text-sm">
      <div className="flex flex-wrap items-center gap-3">
        <Tabs
          value={scope}
          onValueChange={(v) => {
            setScope(v as 'lecture' | 'week')
            setSubject(null)
          }}
        >
          <TabsList>
            <TabsTrigger value="week">A week</TabsTrigger>
            <TabsTrigger value="lecture">One lecture</TabsTrigger>
          </TabsList>
        </Tabs>
      </div>
      <div className="flex flex-wrap items-end gap-3">
        {scope === 'week' ? (
          <>
            <div className="space-y-1.5">
              <Label htmlFor="recap-week">Week</Label>
              <Select value={weekStart || undefined} onValueChange={setWeek}>
                <SelectTrigger id="recap-week" className="w-48">
                  <SelectValue placeholder="No lectures yet" />
                </SelectTrigger>
                <SelectContent>
                  {weeks.map((w) => (
                    <SelectItem key={w.week_start} value={w.week_start}>
                      {weekLabel(w.week_start)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="recap-subject">Subjects</Label>
              <Select value={subjectId} onValueChange={setSubject}>
                <SelectTrigger id="recap-subject" className="w-60">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All subjects that week</SelectItem>
                  {subjects
                    .filter((s) => weekSubjects.includes(s.name))
                    .map((s) => (
                      <SelectItem key={s.id} value={String(s.id)}>
                        {s.name}
                      </SelectItem>
                    ))}
                </SelectContent>
              </Select>
            </div>
          </>
        ) : (
          <>
            <div className="space-y-1.5">
              <Label htmlFor="recap-subject">Subject</Label>
              <Select
                value={subjectId || undefined}
                onValueChange={(v) => {
                  setSubject(v)
                  setLecture(null)
                }}
              >
                <SelectTrigger id="recap-subject" className="w-60">
                  <SelectValue placeholder="Pick a subject" />
                </SelectTrigger>
                <SelectContent>
                  {subjects.map((s) => (
                    <SelectItem key={s.id} value={String(s.id)}>
                      {s.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="recap-lecture">Lecture</Label>
              <Select value={lectureId || undefined} onValueChange={setLecture}>
                <SelectTrigger id="recap-lecture" className="w-64">
                  <SelectValue placeholder="No lectures" />
                </SelectTrigger>
                <SelectContent>
                  {lectures.map((l) => (
                    <SelectItem key={l.id} value={String(l.id)}>
                      {l.title}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </>
        )}
        <div className="space-y-1.5">
          <Label htmlFor="recap-minutes">Length</Label>
          <Select value={minutes} onValueChange={setMinutes}>
            <SelectTrigger id="recap-minutes" className="w-28">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {LENGTHS.map((m) => (
                <SelectItem key={m} value={String(m)}>
                  ~{m} min
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Button onClick={() => create.mutate()} disabled={!ready || create.isPending}>
          {create.isPending ? <Loader2 className="animate-spin" /> : <Plus />} Make recap
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        About $0.03–0.06 with Claude; the voice is free. Making it takes a minute or two.
      </p>
    </section>
  )
}

function AutoRecap() {
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: ['recap-settings'], queryFn: api.recapSettings })
  const save = useMutation({
    mutationFn: api.putRecapSettings,
    onSuccess: (d) => qc.setQueryData(['recap-settings'], d),
    onError: (e: Error) => toast.error(e.message),
  })
  if (!data) return null
  return (
    <div className="mt-3 flex items-center gap-2 px-1 text-sm">
      <Switch
        id="recap-auto"
        checked={data.auto_lecture}
        onCheckedChange={(auto_lecture) => save.mutate({ auto_lecture })}
      />
      <Label htmlFor="recap-auto">Make a recap of every new lecture automatically</Label>
    </div>
  )
}
