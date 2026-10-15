import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CircleHelp, Mic, MicOff, Presentation, Radio, Square, Star } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link, useMatch } from 'react-router'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useEvent } from '@/hooks/useEvents'
import { api } from '@/lib/api'
import { formatDuration } from '@/lib/dates'
import type { Level, RecorderStatus, SlideCaptureStatus } from '@/lib/types'
import { cn } from '@/lib/utils'

function prettyHotkey(combo?: string) {
  if (!combo) return ''
  return combo
    .split('+')
    .map((k) => k.replace(/[<>]/g, ''))
    .map((k) => (k.length <= 3 ? k.toUpperCase() : k[0].toUpperCase() + k.slice(1)))
    .join('+')
}

function Meter({ label, level }: { label: string; level?: Level }) {
  // Map -60..0 dBFS to 0..100 %.
  const pct = (db: number) => Math.max(0, Math.min(100, ((db + 60) / 60) * 100))
  const rms = level ? pct(level.rms_db) : 0
  const peak = level ? pct(level.peak_db) : 0
  return (
    <div className="flex items-center gap-2">
      <span className="w-10 shrink-0 text-[10px] uppercase tracking-wide text-muted-foreground">{label}</span>
      <div className="relative h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
        <div
          className={cn(
            'h-full rounded-full transition-[width] duration-100',
            peak > 95 ? 'bg-red-500' : 'bg-emerald-500',
          )}
          style={{ width: `${rms}%` }}
        />
        <div className="absolute top-0 h-full w-0.5 bg-foreground/50" style={{ left: `${peak}%` }} />
      </div>
    </div>
  )
}

function SlideLine({ status }: { status: SlideCaptureStatus }) {
  return (
    <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
      <Presentation className="size-3.5" />
      {status.source ? (
        <span>
          {status.count} slide{status.count === 1 ? '' : 's'} · {status.source}
        </span>
      ) : (
        <span
          className="text-amber-600 dark:text-amber-400"
          title="Open the Zoom meeting window, or pick a monitor in Settings"
        >
          Waiting for the Zoom window · {status.count} so far
        </span>
      )}
    </div>
  )
}

export function RecorderPanel() {
  const qc = useQueryClient()
  const { data: status } = useQuery({ queryKey: ['recorder'], queryFn: api.recorder })
  const { data: subjects = [] } = useQuery({ queryKey: ['subjects'], queryFn: api.subjects })
  const { data: system } = useQuery({ queryKey: ['system'], queryFn: api.system })
  const routeSubject = useMatch('/subjects/:id')?.params.id
  const { data: tt } = useQuery({
    queryKey: ['timetable-now'],
    queryFn: api.timetableNow,
    refetchInterval: 60_000,
  })
  const slot = tt?.current

  const [picked, setPicked] = useState<string | null>(null)
  const [mic, setMic] = useState<boolean | null>(null)
  const [slidesOn, setSlidesOn] = useState<boolean | null>(null)
  const [slideStatus, setSlideStatus] = useState<SlideCaptureStatus | null>(null)
  const [levels, setLevels] = useState<Record<string, Level>>({})
  const [elapsed, setElapsed] = useState(0)
  const [noticeFor, setNoticeFor] = useState<number | null>(null)

  const subjectId =
    picked ??
    (slot?.subject_id ? String(slot.subject_id) : undefined) ??
    routeSubject ??
    (status?.last_subject_id ? String(status.last_subject_id) : undefined) ??
    (subjects[0] ? String(subjects[0].id) : undefined)
  const micOn = mic ?? system?.recorder.mic_default ?? false
  const captureSlides = slidesOn ?? system?.recorder.slides_default ?? true

  useEvent('recorder.state', (s: RecorderStatus) => {
    qc.setQueryData(['recorder'], s)
    if (!s.recording) setLevels({})
    setSlideStatus(s.slides ?? null)
  })
  useEvent('recorder.slides', (d: SlideCaptureStatus) => setSlideStatus({ count: d.count, source: d.source }))
  useEvent('recorder.level', (d: { elapsed_s: number; levels: Record<string, Level> }) => {
    setLevels(d.levels)
    setElapsed(d.elapsed_s)
  })

  // Tick the clock locally between level events (and before the first one arrives).
  useEffect(() => {
    if (!status?.recording) return
    const startedAt = Date.now() - (status.elapsed_s ?? 0) * 1000
    const id = setInterval(() => setElapsed((Date.now() - startedAt) / 1000), 500)
    return () => clearInterval(id)
  }, [status?.recording, status?.elapsed_s])

  const start = useMutation({
    mutationFn: (sid: number) =>
      api.startRecording({
        subject_id: sid,
        mic: micOn,
        slides: captureSlides,
        kind: slot?.subject_id === sid ? slot.kind : undefined,
      }),
    onSuccess: (s) => {
      qc.setQueryData(['recorder'], s)
      setSlideStatus(s.slides ?? null)
    },
    onError: (e: Error) => toast.error(e.message),
  })
  const stop = useMutation({
    mutationFn: api.stopRecording,
    onError: (e: Error) => toast.error(e.message),
  })
  const mark = useMutation({
    mutationFn: api.bookmark,
    onError: (e: Error) => toast.error(e.message),
  })

  const ack = useMutation({
    mutationFn: (sid: number) => api.updateSubject(sid, { recording_notice_ack: true }),
    onSuccess: (_, sid) => {
      qc.invalidateQueries({ queryKey: ['subjects'] })
      start.mutate(sid)
    },
  })

  const onRecord = (sid = Number(subjectId)) => {
    const subject = subjects.find((s) => s.id === sid)
    if (!subject) return toast.error('Create a subject first')
    if (!subject.recording_notice_ack) return setNoticeFor(sid)
    start.mutate(sid)
  }

  // "Record" on a timetable reminder toast (LiveUpdates) asks the panel, so the notice still shows.
  const recordRef = useRef(onRecord)
  recordRef.current = onRecord
  useEffect(() => {
    const fn = (e: Event) => recordRef.current((e as CustomEvent<number>).detail)
    window.addEventListener('lecturelab:record', fn)
    return () => window.removeEventListener('lecturelab:record', fn)
  }, [])

  const hk = system?.hotkeys

  if (status?.recording) {
    return (
      <div className="mx-2 mb-2 space-y-2 rounded-lg border border-red-500/30 bg-red-500/5 p-3">
        <div className="flex items-center gap-2">
          <span className="relative flex size-2.5">
            <span className="absolute inline-flex size-full animate-ping rounded-full bg-red-500 opacity-60" />
            <span className="relative inline-flex size-2.5 rounded-full bg-red-500" />
          </span>
          <span className="text-sm font-medium">Recording</span>
          <span className="ml-auto font-mono text-sm tabular-nums">{formatDuration(elapsed)}</span>
        </div>
        <div className="truncate text-xs text-muted-foreground" title={status.subject_name}>
          {status.subject_name}
        </div>
        <div className="space-y-1">
          <Meter label="System" level={levels.system} />
          {status.mic && <Meter label="Mic" level={levels.mic} />}
        </div>
        {(slideStatus ?? status.slides) && <SlideLine status={(slideStatus ?? status.slides)!} />}
        <div className="flex gap-1">
          <Button
            size="xs"
            variant="outline"
            className="flex-1"
            title={prettyHotkey(hk?.important)}
            onClick={() => mark.mutate('important')}
          >
            <Star /> Important
          </Button>
          <Button
            size="xs"
            variant="outline"
            className="flex-1"
            title={prettyHotkey(hk?.confused)}
            onClick={() => mark.mutate('confused')}
          >
            <CircleHelp /> Confused
          </Button>
        </div>
        <Button size="xs" variant="ghost" className="w-full justify-start" asChild>
          <Link to="/live">
            <Radio className={status.live ? 'text-red-500' : undefined} />
            {status.live ? 'Live transcript on: open' : 'Live transcript & quick questions'}
          </Link>
        </Button>
        <Button
          size="sm"
          variant="destructive"
          className="w-full"
          disabled={stop.isPending}
          title={prettyHotkey(hk?.start_stop)}
          onClick={() => stop.mutate()}
        >
          <Square className="fill-current" /> Stop
        </Button>
        {status.free_gb < 5 && (
          <p className="text-xs text-amber-600 dark:text-amber-400">Only {status.free_gb} GB free</p>
        )}
      </div>
    )
  }

  return (
    <div className="mx-2 mb-2 space-y-2 rounded-lg border bg-background/50 p-3">
      <Select value={subjectId} onValueChange={setPicked}>
        <SelectTrigger size="sm" className="w-full">
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
      {slot && (
        <p className="truncate text-xs text-muted-foreground" title={slot.summary}>
          On now: {slot.subject_name}
          {slot.kind && ` (${slot.kind})`}
        </p>
      )}
      <div className="flex gap-1">
        <Button
          size="sm"
          className="flex-1 bg-red-600 text-white hover:bg-red-700"
          disabled={!subjectId || start.isPending}
          onClick={() => onRecord()}
        >
          <span className="size-2 rounded-full bg-white" /> Record
        </Button>
        <Button
          size="icon-sm"
          variant={micOn ? 'secondary' : 'ghost'}
          aria-label={micOn ? 'Mic on' : 'Mic off'}
          title={micOn ? 'Mic is mixed in' : 'System audio only'}
          onClick={() => setMic(!micOn)}
        >
          {micOn ? <Mic /> : <MicOff />}
        </Button>
        <Button
          size="icon-sm"
          variant={captureSlides ? 'secondary' : 'ghost'}
          aria-label={captureSlides ? 'Slide capture on' : 'Slide capture off'}
          title={captureSlides ? 'Captures the Zoom window when the slide changes' : 'No slide capture'}
          onClick={() => setSlidesOn(!captureSlides)}
        >
          <Presentation className={captureSlides ? undefined : 'opacity-50'} />
        </Button>
      </div>
      {hk?.enabled && (
        <p className="text-[11px] leading-tight text-muted-foreground">
          {prettyHotkey(hk.start_stop)} start/stop · {prettyHotkey(hk.important)} important ·{' '}
          {prettyHotkey(hk.confused)} confused
        </p>
      )}

      <AlertDialog open={noticeFor !== null} onOpenChange={(o) => !o && setNoticeFor(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Before you record</AlertDialogTitle>
            <AlertDialogDescription>
              Recording a lecture may need the lecturer&apos;s OK, and classmates may be audible. LectureLab
              keeps recordings on this computer and never shares them. This reminder appears once per subject.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (noticeFor !== null) ack.mutate(noticeFor)
                setNoticeFor(null)
              }}
            >
              Understood, record
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
