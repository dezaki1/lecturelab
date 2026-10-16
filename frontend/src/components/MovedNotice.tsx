import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowRightLeft, Loader2, Sparkles } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import type { LectureDetail } from '@/lib/types'

/** After a lecture moved to another subject: its study material was written for the old one
 * (that subject's instructions, vocabulary and earlier lectures), so offer to rewrite it. */
export function MovedNotice({ lecture, subjectName }: { lecture: LectureDetail; subjectName?: string }) {
  const qc = useQueryClient()
  const refresh = () => qc.invalidateQueries({ queryKey: ['lecture', lecture.id] })
  const regenerate = useMutation({
    mutationFn: () => api.generate(lecture.id),
    onSuccess: () => {
      refresh()
      toast.success('Rewriting the study material for this subject…')
    },
    onError: (e: Error) => toast.error(e.message),
  })
  const dismiss = useMutation({
    mutationFn: () => api.updateLecture(lecture.id, { dismiss_moved: true }),
    onSuccess: (d) => qc.setQueryData(['lecture', lecture.id], d),
    onError: (e: Error) => toast.error(e.message),
  })
  if (!lecture.moved_from) return null
  return (
    <div className="mt-4 flex flex-wrap items-center gap-3 rounded-lg border border-amber-500/40 bg-amber-500/5 p-3 text-sm">
      <ArrowRightLeft className="size-4 shrink-0 text-amber-600 dark:text-amber-400" />
      <p className="min-w-0 flex-1">
        Moved here from <b>{lecture.moved_from.name}</b>. Its cards, questions and notes now belong to{' '}
        {subjectName ?? 'this subject'}, and both courses&apos; overviews are being rebuilt. The study
        material itself was written as part of {lecture.moved_from.name} (its vocabulary and earlier
        lectures): rewrite it for this subject? Cards you have reviewed are kept.
      </p>
      <div className="flex gap-2">
        <Button size="sm" onClick={() => regenerate.mutate()} disabled={regenerate.isPending}>
          {regenerate.isPending ? <Loader2 className="animate-spin" /> : <Sparkles />} Rewrite (~$0.30)
        </Button>
        <Button size="sm" variant="ghost" onClick={() => dismiss.mutate()} disabled={dismiss.isPending}>
          Keep as it is
        </Button>
      </div>
    </div>
  )
}
