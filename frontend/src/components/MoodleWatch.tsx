import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Loader2, RefreshCw, Trash2, Tv } from 'lucide-react'
import { useEffect, useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { useEvent } from '@/hooks/useEvents'
import { api } from '@/lib/api'
import type { WatchRecording } from '@/lib/types'

const ago = (iso: string) => {
  const mins = (Date.now() - new Date(iso).getTime()) / 60_000
  if (mins < 2) return 'just now'
  if (mins < 120) return `${Math.round(mins)} min ago`
  if (mins < 48 * 60) return `${Math.round(mins / 60)} h ago`
  return `${Math.round(mins / 1440)} days ago`
}
const minutes = (s: number) => `${Math.round(s / 60)} min`
const day = (iso: string | null) =>
  iso
    ? new Date(iso + 'T12:00').toLocaleDateString(undefined, {
        weekday: 'short',
        day: 'numeric',
        month: 'short',
      })
    : ''

function useWatch() {
  const qc = useQueryClient()
  useEvent('watch', () => qc.invalidateQueries({ queryKey: ['watch'] }))
  return useQuery({ queryKey: ['watch'], queryFn: api.watch })
}

/** Recordings the first scan of a watched Moodle course found: pick which to download. */
export function MoodleReview({ compact = false }: { compact?: boolean }) {
  const qc = useQueryClient()
  const { data } = useWatch()
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const review = data?.review.filter((r) => r.status === 'review') ?? []
  const waiting = data?.review.filter((r) => r.status === 'wanted') ?? []
  const ids = review.map((r) => r.episode_id).join()
  useEffect(() => {
    // Preselect everything that doesn't look like something you already have.
    setPicked(new Set(review.filter((r) => !r.have_hint).map((r) => r.episode_id)))
  }, [ids]) // eslint-disable-line react-hooks/exhaustive-deps
  const decide = useMutation({
    mutationFn: () =>
      api.watchDecide({
        get: [...picked],
        skip: review.map((r) => r.episode_id).filter((id) => !picked.has(id)),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['watch'] })
      toast.success(
        picked.size
          ? `Getting ${picked.size} recording${picked.size > 1 ? 's' : ''}: the browser extension fetches them within a minute.`
          : 'Skipped',
      )
    },
    onError: (e: Error) => toast.error(e.message),
  })
  if (!review.length && !waiting.length) return null
  const bySubject = new Map<string, WatchRecording[]>()
  for (const r of review) bySubject.set(r.subject_name, [...(bySubject.get(r.subject_name) ?? []), r])
  const toggle = (id: string) =>
    setPicked((p) => {
      const n = new Set(p)
      if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })

  return (
    <section className={compact ? 'mt-6 rounded-lg border bg-card p-5 text-sm' : 'space-y-3'}>
      {compact && (
        <h2 className="flex items-center gap-2 font-semibold">
          <Tv className="size-4" /> Recordings found on Moodle
        </h2>
      )}
      {waiting.length > 0 && (
        <p className="mt-2 flex items-center gap-2 text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> Waiting for the browser extension to fetch{' '}
          {waiting.length} recording{waiting.length > 1 ? 's' : ''} (Chrome/Edge must be open).
        </p>
      )}
      {review.length > 0 && (
        <>
          <p className="mt-2 text-muted-foreground">
            These were already online when you started watching the course. Tick the ones to download; new
            recordings come in by themselves from now on.
          </p>
          {[...bySubject].map(([subject, recs]) => (
            <div key={subject} className="mt-3">
              <h3 className="font-medium">{subject}</h3>
              <ul className="mt-1 space-y-1">
                {recs.map((r) => (
                  <li key={r.episode_id}>
                    <label className="flex cursor-pointer items-start gap-2 rounded px-1 py-0.5 hover:bg-muted/50">
                      <input
                        type="checkbox"
                        className="mt-0.5 size-4 accent-primary"
                        checked={picked.has(r.episode_id)}
                        onChange={() => toggle(r.episode_id)}
                      />
                      <span className="min-w-0 flex-1">
                        {r.title || 'Recording'}{' '}
                        <span className="text-muted-foreground">
                          · {[day(r.recorded_on), minutes(r.duration_s)].filter(Boolean).join(' · ')}
                        </span>
                        {r.have_hint && (
                          <span className="block text-xs text-amber-700 dark:text-amber-400">
                            {r.have_hint}
                          </span>
                        )}
                      </span>
                    </label>
                  </li>
                ))}
              </ul>
            </div>
          ))}
          <div className="mt-4 flex flex-wrap gap-2">
            <Button size="sm" onClick={() => decide.mutate()} disabled={decide.isPending}>
              {decide.isPending && <Loader2 className="animate-spin" />}
              {picked.size ? `Get ${picked.size} selected` : 'Skip all'}
            </Button>
            {picked.size > 0 && picked.size < review.length && (
              <span className="self-center text-xs text-muted-foreground">
                The others are skipped (you can watch them on Moodle any time).
              </span>
            )}
          </div>
        </>
      )}
    </section>
  )
}

/** Settings card: watched Moodle courses, last check, problems, "Check now". */
export function MoodleWatchSettings() {
  const qc = useQueryClient()
  const { data } = useWatch()
  const check = useMutation({
    mutationFn: api.watchCheck,
    onSuccess: () => toast.success('Checking within a minute (needs Chrome/Edge open with the extension)'),
  })
  const remove = useMutation({
    mutationFn: api.unwatchCourse,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['watch'] }),
    onError: (e: Error) => toast.error(e.message),
  })
  if (!data) return null
  return (
    <section id="moodle" className="mt-6 space-y-4 rounded-lg border bg-card p-5 text-sm">
      <div>
        <h2 className="flex items-center gap-2 font-semibold">
          <Tv className="size-4" /> Moodle recordings
        </h2>
        <p className="mt-1 text-muted-foreground">
          The LectureLab browser extension checks these courses for new Opencast recordings (hourly, and every
          15 minutes in the hours after a lecture) and downloads them into the right subject. To add a course,
          open it on Moodle and click the extension: <b>Watch this course</b>.
        </p>
      </div>
      {data.courses.length === 0 ? (
        <p className="text-muted-foreground">No courses watched yet.</p>
      ) : (
        <ul className="divide-y rounded-md border">
          {data.courses.map((c) => (
            <li key={c.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
              <div className="min-w-48 flex-1">
                <div className="font-medium">{c.subject_name}</div>
                <div className="truncate text-xs text-muted-foreground" title={c.name}>
                  {c.name || `Moodle course ${c.moodle_id}`}
                </div>
              </div>
              <div className="text-xs text-muted-foreground">
                {c.counts.queued + c.counts.have} in LectureLab
                {c.counts.review > 0 && ` · ${c.counts.review} to review`}
                {' · '}
                {c.last_scan_at ? `checked ${ago(c.last_scan_at)}` : 'not checked yet'}
              </div>
              {c.last_error && (
                <div className="flex w-full items-center gap-1 text-xs text-amber-700 dark:text-amber-400">
                  <AlertTriangle className="size-3.5" /> {c.last_error}
                </div>
              )}
              <Button
                size="icon-sm"
                variant="ghost"
                title="Stop watching"
                onClick={() => remove.mutate(c.id)}
                disabled={remove.isPending}
              >
                <Trash2 />
              </Button>
            </li>
          ))}
        </ul>
      )}
      <Button size="sm" variant="outline" onClick={() => check.mutate()} disabled={!data.courses.length}>
        <RefreshCw /> Check now
      </Button>
      <MoodleReview />
    </section>
  )
}
