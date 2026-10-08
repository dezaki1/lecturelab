import { useMutation, useQueryClient } from '@tanstack/react-query'
import { History, Pencil } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'

import { Markdown } from '@/components/Markdown'
import { RegenerateButton } from '@/components/materials/common'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Textarea } from '@/components/ui/textarea'
import { api } from '@/lib/api'
import type { Note } from '@/lib/types'

const fmt = (iso: string) =>
  new Date(iso).toLocaleString(undefined, {
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  })

/** A generated Markdown section: read, edit in place, regenerate, browse/restore versions. */
export function NoteView({ lectureId, note, kind }: { lectureId: number; note?: Note; kind: string }) {
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [preview, setPreview] = useState<Note | null>(null)

  const invalidate = () => qc.invalidateQueries({ queryKey: ['materials', lectureId] })
  const save = useMutation({
    mutationFn: () => api.editNote(note!.id, draft),
    onSuccess: () => {
      setEditing(false)
      invalidate()
      toast.success('Saved')
    },
    onError: (e: Error) => toast.error(e.message),
  })
  const restore = useMutation({
    mutationFn: (id: number) => api.restoreNote(id),
    onSuccess: () => {
      setPreview(null)
      invalidate()
    },
  })

  if (!note) {
    return (
      <div className="flex items-center justify-between py-6 text-sm text-muted-foreground">
        <span>Not generated yet.</span>
        <RegenerateButton lectureId={lectureId} kind={kind} label="Generate" />
      </div>
    )
  }

  const shown = preview ?? note
  return (
    <div>
      <div className="mb-3 flex items-center gap-1 text-xs text-muted-foreground">
        <span>
          v{shown.version}
          {shown.edited_by_me ? ' · edited by you' : shown.model ? ` · ${shown.model}` : ''}
        </span>
        <div className="ml-auto flex items-center gap-1">
          {note.versions.length > 1 && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button size="xs" variant="ghost">
                  <History /> Versions
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuLabel>Version history</DropdownMenuLabel>
                <DropdownMenuSeparator />
                {note.versions.map((v) => (
                  <DropdownMenuItem
                    key={v.id}
                    onClick={async () => setPreview(v.id === note.id ? null : await api.note(v.id))}
                  >
                    v{v.version} · {v.edited_by_me ? 'your edit' : 'generated'} · {fmt(v.created_at)}
                    {v.id === note.id && ' (current)'}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
          {!editing && !preview && (
            <Button
              size="xs"
              variant="ghost"
              onClick={() => {
                setDraft(note.content_md)
                setEditing(true)
              }}
            >
              <Pencil /> Edit
            </Button>
          )}
          {!editing && <RegenerateButton lectureId={lectureId} kind={kind} warnEdited={note.edited_by_me} />}
        </div>
      </div>

      {preview && (
        <div className="mb-3 flex items-center gap-2 rounded-md border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-sm">
          Viewing v{preview.version} (not current).
          <Button size="xs" className="ml-auto" onClick={() => restore.mutate(preview.id)}>
            Restore this version
          </Button>
          <Button size="xs" variant="ghost" onClick={() => setPreview(null)}>
            Back to current
          </Button>
        </div>
      )}

      {editing ? (
        <div className="grid gap-3 lg:grid-cols-2">
          <Textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            className="min-h-[60vh] font-mono text-sm"
            onKeyDown={(e) => {
              if (e.key === 's' && (e.ctrlKey || e.metaKey)) {
                e.preventDefault()
                save.mutate()
              }
            }}
          />
          <div className="max-h-[60vh] overflow-y-auto rounded-md border p-4">
            <Markdown>{draft}</Markdown>
          </div>
          <div className="flex gap-2 lg:col-span-2">
            <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending}>
              Save (Ctrl+S)
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <Markdown>{shown.content_md}</Markdown>
      )}
    </div>
  )
}
