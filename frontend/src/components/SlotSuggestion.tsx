import { useMutation, useQueryClient } from '@tanstack/react-query'
import { CalendarClock, Loader2 } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import type { LectureDetail } from '@/lib/types'

/** The timetable says this lecture took place during another subject's slot (e.g. a tablet
 * recording imported from the wrong subject page): offer to move it there. */
export function SlotSuggestion({ lecture }: { lecture: LectureDetail }) {
  const qc = useQueryClient()
  const hint = lecture.slot_suggestion
  const done = (d: LectureDetail) => {
    qc.setQueryData(['lecture', lecture.id], d)
    qc.invalidateQueries({ queryKey: ['lectures'] })
    qc.invalidateQueries({ queryKey: ['subjects'] })
  }
  const move = useMutation({
    mutationFn: () =>
      api.updateLecture(lecture.id, {
        subject_id: hint!.subject_id,
        ...(hint!.kind ? { kind: hint!.kind } : {}),
      }),
    onSuccess: (d) => {
      done(d)
      toast.success(
        d.move_queued
          ? `Moving to ${hint!.subject_name} after the current step…`
          : `Moved to ${hint!.subject_name}`,
      )
    },
    onError: (e: Error) => toast.error(e.message),
  })
  const keep = useMutation({
    mutationFn: () => api.updateLecture(lecture.id, { dismiss_slot: true }),
    onSuccess: done,
    onError: (e: Error) => toast.error(e.message),
  })
  if (lecture.move_queued)
    return (
      <p className="mt-4 flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" /> Moving to its subject after the current step…
      </p>
    )
  if (!hint) return null
  const when = new Date(hint.start).toLocaleString(undefined, {
    weekday: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })
  return (
    <div className="mt-4 flex flex-wrap items-center gap-3 rounded-lg border border-sky-500/40 bg-sky-500/5 p-3 text-sm">
      <CalendarClock className="size-4 shrink-0 text-sky-600 dark:text-sky-400" />
      <p className="min-w-64 flex-1">
        Your timetable has <b>{hint.subject_name}</b> at this time ({hint.summary}, {when}). Is this that
        lecture?
      </p>
      <div className="flex gap-2">
        <Button size="sm" onClick={() => move.mutate()} disabled={move.isPending}>
          {move.isPending && <Loader2 className="animate-spin" />} Move to {hint.subject_name}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => keep.mutate()} disabled={keep.isPending}>
          It belongs here
        </Button>
      </div>
    </div>
  )
}
