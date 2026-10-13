import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Camera, Check, FileUp, Loader2, Pencil, RefreshCw, Trash2, TriangleAlert, X } from 'lucide-react'
import { useRef, useState } from 'react'
import { toast } from 'sonner'

import { Markdown } from '@/components/Markdown'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { useEvent } from '@/hooks/useEvents'
import { api, uploadDocument } from '@/lib/api'
import type { HandwrittenNotes as Notes, HandwrittenPage } from '@/lib/types'

const ACCEPT = '.pdf,application/pdf,image/jpeg,image/png,image/webp'

/** Lecture tab "My notes": upload PDFs/photos of handwritten notes, see and fix the transcription. */
export function HandwrittenNotes({ lectureId }: { lectureId: number }) {
  const qc = useQueryClient()
  const files = useRef<HTMLInputElement>(null)
  const camera = useRef<HTMLInputElement>(null)
  const key = ['handwritten', lectureId]
  const { data: notes = [] } = useQuery({
    queryKey: key,
    queryFn: () => api.handwritten(lectureId),
    refetchInterval: (q) => (q.state.data?.some((n) => n.processing) ? 2500 : false),
  })
  useEvent('lecture', (d: { id: number }) => d.id === lectureId && qc.invalidateQueries({ queryKey: key }))
  const refresh = () => qc.invalidateQueries({ queryKey: key })
  const upload = useMutation({
    mutationFn: async (list: File[]) => {
      for (const f of list) await uploadDocument(lectureId, f, 'handwritten')
      return list.length
    },
    onSuccess: (n) => {
      refresh()
      toast.success(n === 1 ? 'Uploaded: reading your notes…' : `${n} files uploaded: reading your notes…`)
    },
    onError: (e: Error) => toast.error(e.message),
  })
  const pick = (input: HTMLInputElement) => {
    const list = Array.from(input.files ?? [])
    input.value = ''
    if (list.length) upload.mutate(list)
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant="outline"
          onClick={() => files.current?.click()}
          disabled={upload.isPending}
        >
          {upload.isPending ? <Loader2 className="animate-spin" /> : <FileUp />} Upload PDF or photos
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={() => camera.current?.click()}
          disabled={upload.isPending}
        >
          <Camera /> Take a photo
        </Button>
        <span className="text-xs text-muted-foreground">
          Notes from your tablet (Samsung Notes, GoodNotes… as PDF) or photos of paper. Read by AI, then
          searchable and used for this lecture&apos;s study material.
        </span>
        <input
          ref={files}
          type="file"
          accept={ACCEPT}
          multiple
          hidden
          onChange={(e) => pick(e.currentTarget)}
        />
        <input
          ref={camera}
          type="file"
          accept="image/*"
          capture="environment"
          hidden
          onChange={(e) => pick(e.currentTarget)}
        />
      </div>
      {notes.length === 0 && (
        <p className="py-6 text-center text-sm text-muted-foreground">
          No handwritten notes for this lecture yet.
        </p>
      )}
      {notes.map((n) => (
        <NoteFile key={n.id} n={n} onChange={refresh} />
      ))}
    </div>
  )
}

function NoteFile({ n, onChange }: { n: Notes; onChange: () => void }) {
  const onError = (e: Error) => toast.error(e.message)
  const reread = useMutation({ mutationFn: () => api.placeDocument(n.id), onSuccess: onChange, onError })
  const remove = useMutation({ mutationFn: () => api.deleteDocument(n.id), onSuccess: onChange, onError })
  return (
    <section className="rounded-lg border bg-card">
      <div className="flex flex-wrap items-center gap-2 border-b px-4 py-2 text-sm">
        <a
          href={api.documentFileUrl(n.id)}
          target="_blank"
          rel="noreferrer"
          className="font-medium hover:underline"
        >
          {n.title}
        </a>
        {n.processing ? (
          <span className="flex items-center gap-1 text-xs text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" /> reading…
          </span>
        ) : n.method === 'ocr' ? (
          <span
            className="flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400"
            title="The local AI model can't read images, so local OCR was used; it reads handwriting poorly."
          >
            <TriangleAlert className="size-3.5" /> local OCR: check carefully
          </span>
        ) : (
          <span className="text-xs text-muted-foreground">
            {n.pages.length} page{n.pages.length === 1 ? '' : 's'}
            {n.cost_usd > 0 && ` · $${n.cost_usd.toFixed(3)}`}
          </span>
        )}
        <div className="ml-auto flex gap-1">
          <Button
            size="xs"
            variant="ghost"
            onClick={() => reread.mutate()}
            disabled={n.processing || reread.isPending}
            title="Read the pages again (your corrections are replaced)"
          >
            <RefreshCw /> Read again
          </Button>
          <Button
            size="icon-xs"
            variant="ghost"
            aria-label="Delete notes"
            onClick={() => remove.mutate()}
            disabled={n.processing}
          >
            <Trash2 />
          </Button>
        </div>
      </div>
      <ol className="divide-y">
        {n.pages.map((p) => (
          <PageRow key={p.page} docId={n.id} p={p} onChange={onChange} />
        ))}
      </ol>
    </section>
  )
}

function PageRow({ docId, p, onChange }: { docId: number; p: HandwrittenPage; onChange: () => void }) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(p.text)
  const save = useMutation({
    mutationFn: () => api.editHandwrittenPage(docId, p.page, text),
    onSuccess: () => {
      setEditing(false)
      onChange()
    },
    onError: (e: Error) => toast.error(e.message),
  })
  const img = api.handwrittenPageUrl(docId, p.page)
  return (
    <li className="grid gap-4 p-4 lg:grid-cols-2">
      <a href={img} target="_blank" rel="noreferrer" className="block">
        <img
          src={img}
          alt={`Page ${p.page}`}
          loading="lazy"
          className="mx-auto max-h-[32rem] max-w-full rounded border bg-white"
        />
      </a>
      <div className="min-w-0 text-sm">
        <div className="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
          <span className="font-medium text-foreground">Page {p.page}</span>
          {p.summary && <span className="truncate">· {p.summary}</span>}
          {p.edited && <span>· corrected by you</span>}
          {!editing && (
            <Button
              size="xs"
              variant="ghost"
              className="ml-auto"
              onClick={() => {
                setText(p.text)
                setEditing(true)
              }}
            >
              <Pencil /> Correct
            </Button>
          )}
        </div>
        {editing ? (
          <div className="space-y-2">
            <Textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={14}
              className="font-mono text-xs"
            />
            <div className="flex gap-1">
              <Button size="xs" onClick={() => save.mutate()} disabled={save.isPending}>
                <Check /> Save
              </Button>
              <Button size="xs" variant="ghost" onClick={() => setEditing(false)}>
                <X /> Cancel
              </Button>
            </div>
          </div>
        ) : p.text.trim() ? (
          <Markdown>{p.text}</Markdown>
        ) : (
          <p className="text-muted-foreground">Nothing readable on this page.</p>
        )}
        {!editing && !p.edited && p.unclear.length > 0 && (
          <p className="mt-2 text-xs text-amber-600 dark:text-amber-400">
            Couldn&apos;t read: {p.unclear.join(' · ')}
          </p>
        )}
      </div>
    </li>
  )
}
