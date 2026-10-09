import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Loader2, RotateCcw, SpellCheck, TriangleAlert } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { api } from '@/lib/api'
import type { LectureDetail } from '@/lib/types'

/** Status of the transcript cleanup pass, with show-changes / undo / run-again controls. */
export function CleanupBar({
  lecture,
  corrected,
  showChanges,
  onShowChanges,
}: {
  lecture: LectureDetail
  corrected: number // lines that currently differ from Whisper's text
  showChanges: boolean
  onShowChanges: (v: boolean) => void
}) {
  const qc = useQueryClient()
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['lecture', lecture.id] })
    qc.invalidateQueries({ queryKey: ['transcript', lecture.id] })
  }
  const run = useMutation({
    mutationFn: () => api.runCleanup(lecture.id),
    onSuccess: refresh,
    onError: (e: Error) => toast.error(e.message),
  })
  const undoAll = useMutation({
    mutationFn: () => api.revertCleanup(lecture.id),
    onSuccess: (r) => {
      refresh()
      onShowChanges(false)
      toast.success(`Restored ${r.reverted} line${r.reverted === 1 ? '' : 's'} as transcribed`)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const stage = lecture.stages.cleanup
  const busy = lecture.status === 'processing' || lecture.status === 'recording'

  let body: React.ReactNode
  if (stage === 'pending' || stage === 'running') {
    body = (
      <span className="flex items-center gap-2">
        <Loader2 className="size-3.5 animate-spin" /> Checking the transcript for misheard terms…
      </span>
    )
  } else if (stage === 'failed') {
    body = (
      <>
        <span className="flex min-w-0 items-center gap-2 text-destructive">
          <TriangleAlert className="size-3.5 shrink-0" />
          <span className="truncate" title={lecture.cleanup_error}>
            Fixing misheard terms failed
          </span>
        </span>
        <Button size="xs" variant="outline" className="ml-auto" onClick={() => run.mutate()} disabled={busy}>
          Retry
        </Button>
      </>
    )
  } else if (stage === 'done') {
    body = (
      <>
        <span className="flex items-center gap-2">
          <SpellCheck className="size-3.5 text-emerald-600 dark:text-emerald-400" />
          {corrected
            ? `${corrected} line${corrected === 1 ? '' : 's'} with misheard terms fixed`
            : 'Checked for misheard terms: nothing to fix'}
        </span>
        <div className="ml-auto flex items-center gap-2">
          {corrected > 0 && (
            <>
              <Switch id="show-changes" size="sm" checked={showChanges} onCheckedChange={onShowChanges} />
              <Label htmlFor="show-changes" className="text-xs font-normal">
                Show changes
              </Label>
              <Button size="xs" variant="ghost" onClick={() => undoAll.mutate()} disabled={undoAll.isPending}>
                <RotateCcw /> Undo all
              </Button>
            </>
          )}
          <Button
            size="xs"
            variant="ghost"
            onClick={() => run.mutate()}
            disabled={busy || run.isPending}
            title="Check again, e.g. after adding a slide PDF or vocabulary"
          >
            Check again
          </Button>
        </div>
      </>
    )
  } else {
    body = (
      <>
        <span>Whisper sometimes mishears technical terms.</span>
        <Button size="xs" variant="outline" className="ml-auto" onClick={() => run.mutate()} disabled={busy}>
          <SpellCheck /> Fix with slides and vocabulary
        </Button>
      </>
    )
  }
  return (
    <div className="mb-1 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border bg-card px-3 py-1.5 text-xs text-muted-foreground">
      {body}
    </div>
  )
}
