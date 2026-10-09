import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FileText, FileUp, Loader2, Trash2 } from 'lucide-react'
import { useRef } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { api, uploadDocument } from '@/lib/api'
import type { LectureDetail } from '@/lib/types'

/** Slide PDFs of a lecture (e.g. from Moodle): add, open, remove; shows placement status. */
export function SlideDocuments({ lecture }: { lecture: LectureDetail }) {
  const qc = useQueryClient()
  const input = useRef<HTMLInputElement>(null)
  const { data: docs = [] } = useQuery({
    queryKey: ['documents', lecture.id, lecture.slide_count],
    queryFn: () => api.documents(lecture.id),
    refetchInterval: (q) => (q.state.data?.some((d) => d.processing) ? 2000 : false),
  })
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['documents', lecture.id] })
    qc.invalidateQueries({ queryKey: ['lecture', lecture.id] })
  }
  const upload = useMutation({
    mutationFn: (file: File) => uploadDocument(lecture.id, file),
    onSuccess: refresh,
    onError: (e: Error) => toast.error(e.message),
  })
  const remove = useMutation({
    mutationFn: api.deleteDocument,
    onSuccess: refresh,
    onError: (e: Error) => toast.error(e.message),
  })
  const transcribed = lecture.stages.transcribe === 'done'

  return (
    <div className="mb-4 rounded-lg border bg-card p-3">
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium">Slide PDFs</span>
        <span className="text-xs text-muted-foreground">
          pages are matched to the moment they were discussed
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
        <Button
          size="xs"
          variant="outline"
          className="ml-auto"
          onClick={() => input.current?.click()}
          disabled={upload.isPending}
        >
          {upload.isPending ? <Loader2 className="animate-spin" /> : <FileUp />} Add PDF
        </Button>
      </div>
      {docs.length > 0 && (
        <ul className="mt-2 divide-y text-sm">
          {docs.map((d) => (
            <li key={d.id} className="flex items-center gap-2 py-1.5">
              <FileText className="size-4 shrink-0 text-muted-foreground" />
              <a
                href={api.documentFileUrl(d.id)}
                target="_blank"
                rel="noreferrer"
                className="min-w-0 truncate hover:underline"
              >
                {d.title}
              </a>
              <span className="shrink-0 text-xs text-muted-foreground">
                {d.processing ? (
                  <span className="flex items-center gap-1">
                    <Loader2 className="size-3 animate-spin" /> reading…
                  </span>
                ) : !transcribed ? (
                  'placed after transcription'
                ) : d.placed ? (
                  `${d.placed} of ${d.pages} pages in this lecture`
                ) : (
                  `no page matched this lecture (${d.pages} pages)`
                )}
              </span>
              <Button
                size="icon-xs"
                variant="ghost"
                className="ml-auto"
                aria-label={`Remove ${d.title}`}
                onClick={() => remove.mutate(d.id)}
                disabled={d.processing}
              >
                <Trash2 />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
