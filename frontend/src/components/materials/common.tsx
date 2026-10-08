import { useMutation, useQueryClient } from '@tanstack/react-query'
import { RefreshCw, TriangleAlert } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'

import { TimeChip } from '@/components/Markdown'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Textarea } from '@/components/ui/textarea'
import { api } from '@/lib/api'
import type { SourceRef } from '@/lib/types'

export function RefChips({ refs }: { refs: SourceRef[] }) {
  return (
    <>
      {refs
        .filter((r) => r.type === 'lecture' && r.start_s != null)
        .slice(0, 3)
        .map((r, i) => (
          <TimeChip key={i} t={r.start_s!} />
        ))}
    </>
  )
}

export function FlaggedBadge({ note }: { note: string }) {
  return (
    <span
      className="inline-flex items-center gap-1 rounded-md bg-amber-500/10 px-1.5 py-0.5 text-xs text-amber-700 dark:text-amber-400"
      title={note || 'Not supported by the cited part of the lecture'}
    >
      <TriangleAlert className="size-3" /> Check this
    </span>
  )
}

/** "Regenerate" with optional extra instructions for one section. */
export function RegenerateButton({
  lectureId,
  kind,
  label = 'Regenerate',
  warnEdited = false,
}: {
  lectureId: number
  kind: string
  label?: string
  warnEdited?: boolean
}) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [text, setText] = useState('')
  const run = useMutation({
    mutationFn: () => api.generate(lectureId, [kind], text),
    onSuccess: () => {
      setOpen(false)
      setText('')
      qc.invalidateQueries({ queryKey: ['lecture', lectureId] })
      toast.info('Regenerating…')
    },
    onError: (e: Error) => toast.error(e.message),
  })
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button size="xs" variant="ghost">
          <RefreshCw /> {label}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 space-y-2">
        <p className="text-sm font-medium">Regenerate this section</p>
        {warnEdited && (
          <p className="text-xs text-amber-700 dark:text-amber-400">
            Your edited version is kept in the version history.
          </p>
        )}
        <Textarea
          rows={3}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Optional: extra instructions, e.g. 'more worked examples' or 'focus on the proofs'"
        />
        <div className="flex justify-end">
          <Button size="sm" onClick={() => run.mutate()} disabled={run.isPending}>
            Regenerate
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  )
}
