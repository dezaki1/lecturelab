import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, Loader2, Pencil, Printer, Sparkles } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'

import { Markdown } from '@/components/Markdown'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { api } from '@/lib/api'
import { downloadPdf } from '@/lib/download'

/** Subject tab: the 1-2 page cheat sheet (write, edit, rewrite, PDF). */
export function CheatSheetPanel({ subjectId }: { subjectId: number }) {
  const qc = useQueryClient()
  const { data } = useQuery({
    queryKey: ['cheatsheet', subjectId],
    queryFn: () => api.cheatsheet(subjectId),
    refetchInterval: (q) => (q.state.data?.building ? 3000 : false),
  })
  const [pages, setPages] = useState('2')
  const [instructions, setInstructions] = useState('')
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const refresh = () => qc.invalidateQueries({ queryKey: ['cheatsheet', subjectId] })
  const build = useMutation({
    mutationFn: () => api.buildCheatsheet(subjectId, { pages: Number(pages), instructions }),
    onSuccess: refresh,
    onError: (e: Error) => toast.error(e.message),
  })
  const save = useMutation({
    mutationFn: () => api.editNote(data!.note!.id, draft),
    onSuccess: () => {
      setEditing(false)
      refresh()
      toast.success('Saved')
    },
    onError: (e: Error) => toast.error(e.message),
  })
  if (!data) return null
  const note = data.note
  const busy = data.building || build.isPending

  const controls = (
    <div className="flex flex-wrap items-center gap-2">
      <Select value={pages} onValueChange={setPages}>
        <SelectTrigger size="sm" className="w-28">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="1">1 page</SelectItem>
          <SelectItem value="2">2 pages</SelectItem>
        </SelectContent>
      </Select>
      <Input
        value={instructions}
        onChange={(e) => setInstructions(e.target.value)}
        placeholder="Optional: what to focus on"
        className="h-8 w-64"
      />
      <Button size="sm" onClick={() => build.mutate()} disabled={busy}>
        {busy ? <Loader2 className="animate-spin" /> : <Sparkles />} {note ? 'Rewrite' : 'Write cheat sheet'}
      </Button>
    </div>
  )

  if (!note)
    return (
      <div className="space-y-3 text-sm text-muted-foreground">
        <p>
          A dense 1–2 page summary of the whole course for the last look before the exam: formulas,
          definitions, algorithms and everything flagged for the exam. Written by Claude from the
          lectures&apos; notes.
        </p>
        {busy ? (
          <p className="flex items-center gap-2">
            <Loader2 className="size-4 animate-spin" /> Writing the cheat sheet…
          </p>
        ) : (
          controls
        )}
      </div>
    )

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted-foreground">
          v{note.version}
          {note.edited_by_me ? ' · edited by you' : note.model ? ` · ${note.model}` : ''}
        </span>
        <div className="ml-auto flex flex-wrap gap-1">
          <Button
            size="sm"
            variant="outline"
            onClick={() =>
              downloadPdf(api.cheatsheetPdfUrl(subjectId), `/print/subjects/${subjectId}/cheatsheet`)
            }
          >
            <Download /> PDF
          </Button>
          <Button size="sm" variant="ghost" asChild>
            <a href={`/print/subjects/${subjectId}/cheatsheet`} target="_blank" rel="noreferrer">
              <Printer /> Print view
            </a>
          </Button>
          {!editing && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setDraft(note.content_md)
                setEditing(true)
              }}
            >
              <Pencil /> Edit
            </Button>
          )}
        </div>
      </div>
      {editing ? (
        <div className="space-y-2">
          <Textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            className="min-h-96 font-mono text-sm"
          />
          <div className="flex gap-2">
            <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending}>
              Save
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <div className="rounded-lg border bg-card p-5 md:columns-2 md:gap-8">
          <Markdown className="text-sm">{note.content_md}</Markdown>
        </div>
      )}
      <div className="mt-4 border-t pt-3">
        {busy ? <p className="text-sm text-muted-foreground">Rewriting…</p> : controls}
      </div>
    </div>
  )
}
