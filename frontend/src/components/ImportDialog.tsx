import { useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, FileAudio, FileVideo, Loader2, Upload, XCircle } from 'lucide-react'
import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from 'react'
import { useMatch, useNavigate } from 'react-router'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { api, uploadImport } from '@/lib/api'

export const IMPORT_EXTENSIONS = [
  'mp4', 'm4a', 'mp3', 'wav', 'webm', 'mkv', 'mov', 'ogg', 'opus', 'flac', 'aac', 'wma',
] // prettier-ignore
export const IMPORT_ACCEPT = IMPORT_EXTENSIONS.map((e) => '.' + e).join(',')

const isSupported = (f: File) => IMPORT_EXTENSIONS.includes(f.name.split('.').pop()?.toLowerCase() ?? '')

function titleFromName(name: string) {
  return name
    .replace(/\.[^.]+$/, '')
    .replace(/_/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

function formatSize(bytes: number) {
  return bytes > 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.max(0.1, bytes / 1e6).toFixed(1)} MB`
}

type Row = {
  file: File
  title: string
  kind: string
  state: 'pending' | 'uploading' | 'done' | 'error'
  progress: number
  error?: string
}

type Ctx = { openImport: (files: File[], subjectId?: number) => void }
const ImportContext = createContext<Ctx>({ openImport: () => {} })
export const useImport = () => useContext(ImportContext)

/** Provides `openImport` and a window-wide drop zone for audio/video files. */
export function ImportProvider({ children }: { children: ReactNode }) {
  const [rows, setRows] = useState<Row[] | null>(null)
  const [subjectHint, setSubjectHint] = useState<number | undefined>()
  const [dragging, setDragging] = useState(false)
  const dragDepth = useRef(0)
  const routeSubject = useMatch('/subjects/:id')?.params.id

  const openImport = useCallback((files: File[], subjectId?: number) => {
    if (!files.length) return
    setSubjectHint(subjectId)
    setRows(
      files.map((file) => ({
        file,
        title: titleFromName(file.name),
        kind: '',
        state: 'pending',
        progress: 0,
        error: isSupported(file) ? undefined : 'Unsupported file type',
      })),
    )
  }, [])

  const hasFiles = (e: React.DragEvent) => Array.from(e.dataTransfer.types).includes('Files')

  return (
    <ImportContext.Provider value={{ openImport }}>
      <div
        className="contents"
        onDragEnter={(e) => {
          if (!hasFiles(e)) return
          dragDepth.current += 1
          setDragging(true)
        }}
        onDragLeave={() => {
          dragDepth.current = Math.max(0, dragDepth.current - 1)
          if (dragDepth.current === 0) setDragging(false)
        }}
        onDragOver={(e) => {
          if (hasFiles(e)) e.preventDefault()
        }}
        onDrop={(e) => {
          if (!hasFiles(e)) return
          e.preventDefault()
          dragDepth.current = 0
          setDragging(false)
          openImport(Array.from(e.dataTransfer.files), routeSubject ? Number(routeSubject) : undefined)
        }}
      >
        {children}
        {dragging && (
          <div className="pointer-events-none fixed inset-0 z-50 flex items-center justify-center bg-background/80 backdrop-blur-sm">
            <div className="flex flex-col items-center gap-3 rounded-xl border-2 border-dashed border-primary/50 px-16 py-12">
              <Upload className="size-8 text-primary" />
              <p className="text-lg font-medium">Drop recordings to import</p>
              <p className="text-sm text-muted-foreground">Audio or video: mp4, m4a, mp3, wav, webm…</p>
            </div>
          </div>
        )}
      </div>
      {rows && (
        <ImportDialog rows={rows} setRows={setRows} subjectHint={subjectHint} onClose={() => setRows(null)} />
      )}
    </ImportContext.Provider>
  )
}

function ImportDialog({
  rows,
  setRows,
  subjectHint,
  onClose,
}: {
  rows: Row[]
  setRows: React.Dispatch<React.SetStateAction<Row[] | null>>
  subjectHint?: number
  onClose: () => void
}) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const { data: subjects = [] } = useQuery({ queryKey: ['subjects'], queryFn: api.subjects })
  const { data: recorder } = useQuery({ queryKey: ['recorder'], queryFn: api.recorder })
  const [picked, setPicked] = useState<string | undefined>()
  const [running, setRunning] = useState(false)

  const subjectId =
    picked ??
    (subjectHint ? String(subjectHint) : undefined) ??
    (recorder?.last_subject_id ? String(recorder.last_subject_id) : undefined) ??
    (subjects[0] ? String(subjects[0].id) : undefined)

  const update = (i: number, patch: Partial<Row>) =>
    setRows((rs) => rs && rs.map((r, j) => (j === i ? { ...r, ...patch } : r)))

  const importable = rows.filter((r) => !r.error || r.state === 'error')
  const finished = rows.length > 0 && rows.every((r) => r.state === 'done' || r.error)
  const anyDone = rows.some((r) => r.state === 'done')

  const run = async () => {
    if (!subjectId) return
    setRunning(true)
    for (let i = 0; i < rows.length; i++) {
      const r = rows[i]
      if (r.state === 'done' || (r.error && r.state !== 'error')) continue
      update(i, { state: 'uploading', progress: 0, error: undefined })
      try {
        await uploadImport(r.file, { subject_id: Number(subjectId), title: r.title, kind: r.kind }, (p) =>
          update(i, { progress: p }),
        )
        update(i, { state: 'done', progress: 1 })
      } catch (e) {
        update(i, { state: 'error', error: (e as Error).message })
      }
    }
    setRunning(false)
    qc.invalidateQueries({ queryKey: ['lectures'] })
    qc.invalidateQueries({ queryKey: ['subjects'] })
  }

  const close = () => {
    if (running) return
    onClose()
    if (anyDone && subjectId) navigate(`/subjects/${subjectId}`)
  }

  return (
    <Dialog open onOpenChange={(o) => !o && close()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Import recordings</DialogTitle>
          <DialogDescription>
            The audio is extracted and stored in LectureLab. Your original files are not changed.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-2">
          <span className="text-sm font-medium">Subject</span>
          <Select value={subjectId} onValueChange={setPicked} disabled={running || finished}>
            <SelectTrigger className="w-full">
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
        </div>

        <ul className="max-h-[50vh] space-y-2 overflow-y-auto">
          {rows.map((r, i) => {
            const Icon = /\.(mp4|webm|mkv|mov)$/i.test(r.file.name) ? FileVideo : FileAudio
            const editable = r.state === 'pending' && !r.error
            return (
              <li key={i} className="rounded-lg border p-3">
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <Icon className="size-4 shrink-0" />
                  <span className="truncate">{r.file.name}</span>
                  <span className="shrink-0">· {formatSize(r.file.size)}</span>
                  <span className="ml-auto shrink-0">
                    {r.state === 'uploading' && (
                      <span className="flex items-center gap-1">
                        <Loader2 className="size-3.5 animate-spin" /> {Math.round(r.progress * 100)}%
                      </span>
                    )}
                    {r.state === 'done' && <CheckCircle2 className="size-4 text-emerald-500" />}
                    {r.error && <XCircle className="size-4 text-destructive" />}
                  </span>
                </div>
                {r.error ? (
                  <p className="mt-1 text-sm text-destructive">{r.error}</p>
                ) : (
                  <div className="mt-2 flex gap-2">
                    <Input
                      value={r.title}
                      disabled={!editable || running}
                      onChange={(e) => update(i, { title: e.target.value })}
                      aria-label="Title"
                    />
                    <Select
                      value={r.kind || 'none'}
                      disabled={!editable || running}
                      onValueChange={(v) => update(i, { kind: v === 'none' ? '' : v })}
                    >
                      <SelectTrigger className="w-32">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="none">Type…</SelectItem>
                        <SelectItem value="VL">VL</SelectItem>
                        <SelectItem value="VO">VO</SelectItem>
                        <SelectItem value="UE">UE</SelectItem>
                        <SelectItem value="other">Other</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                )}
                {r.state === 'uploading' && (
                  <div className="mt-2 h-1 overflow-hidden rounded-full bg-muted">
                    <div
                      className="h-full bg-primary transition-[width]"
                      style={{ width: `${r.progress * 100}%` }}
                    />
                  </div>
                )}
              </li>
            )
          })}
        </ul>

        <DialogFooter>
          {finished ? (
            <Button onClick={close}>Done</Button>
          ) : (
            <>
              <Button variant="ghost" onClick={close} disabled={running}>
                Cancel
              </Button>
              <Button onClick={run} disabled={running || !subjectId || importable.length === 0}>
                {running
                  ? 'Uploading…'
                  : `Import ${importable.length} file${importable.length === 1 ? '' : 's'}`}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
