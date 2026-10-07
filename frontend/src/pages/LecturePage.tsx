import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, CircleHelp, MessageCircleQuestion, Star, Trash2, X } from 'lucide-react'
import { useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { toast } from 'sonner'

import { LectureStatusBadge } from '@/components/LectureStatusBadge'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { api } from '@/lib/api'
import { formatDateTime, formatDuration } from '@/lib/dates'
import type { BookmarkKind, LectureDetail } from '@/lib/types'

// Seek a little before a bookmark: you usually press the hotkey after the moment happened.
const PRE_ROLL_S = 5

const KIND_ICON: Record<BookmarkKind, typeof Star> = {
  important: Star,
  confused: CircleHelp,
  question: MessageCircleQuestion,
}

export function LecturePage() {
  const id = Number(useParams().id)
  const qc = useQueryClient()
  const navigate = useNavigate()
  const audio = useRef<HTMLAudioElement>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)

  const { data: lecture, error } = useQuery({ queryKey: ['lecture', id], queryFn: () => api.lecture(id) })
  const { data: subjects = [] } = useQuery({ queryKey: ['subjects'], queryFn: api.subjects })

  const onSaved = (l: LectureDetail) => {
    qc.setQueryData(['lecture', id], l)
    qc.invalidateQueries({ queryKey: ['lectures'] })
    qc.invalidateQueries({ queryKey: ['subjects'] })
  }
  const update = useMutation({
    mutationFn: (data: Parameters<typeof api.updateLecture>[1]) => api.updateLecture(id, data),
    onSuccess: onSaved,
    onError: (e: Error) => toast.error(e.message),
  })
  const del = useMutation({
    mutationFn: () => api.deleteLecture(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['lectures'] })
      qc.invalidateQueries({ queryKey: ['subjects'] })
      toast.success('Lecture moved to the trash folder')
      navigate(lecture ? `/subjects/${lecture.subject_id}` : '/')
    },
    onError: (e: Error) => toast.error(e.message),
  })
  const addBookmark = useMutation({
    mutationFn: (kind: BookmarkKind) =>
      api.addBookmark(id, { t_s: Math.round((audio.current?.currentTime ?? 0) * 10) / 10, kind }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['lecture', id] }),
  })
  const delBookmark = useMutation({
    mutationFn: (bid: number) => api.deleteBookmark(id, bid),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['lecture', id] }),
  })

  if (error) return <div className="p-10 text-sm text-destructive">{error.message}</div>
  if (!lecture) return null
  const subject = subjects.find((s) => s.id === lecture.subject_id)

  const seek = (t: number) => {
    const el = audio.current
    if (!el) return
    el.currentTime = Math.max(0, t - PRE_ROLL_S)
    void el.play()
  }

  return (
    <div className="mx-auto max-w-4xl px-8 py-8">
      <Link
        to={`/subjects/${lecture.subject_id}`}
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" />
        {subject && <span className="size-2 rounded-full" style={{ background: subject.color }} />}
        {subject?.name}
      </Link>

      <header className="mt-3 flex items-start gap-3">
        <span className="mt-2 font-mono text-sm text-muted-foreground">L{lecture.number}</span>
        <div className="min-w-0 flex-1">
          <TitleEditor
            key={lecture.title}
            value={lecture.title}
            onSave={(title) => update.mutate({ title })}
          />
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
            <span>{formatDateTime(lecture.started_at)}</span>
            {lecture.duration_s != null && <span>{formatDuration(lecture.duration_s)}</span>}
            <span>{lecture.source === 'import' ? 'Imported' : 'Recorded'}</span>
            <LectureStatusBadge lecture={lecture} />
          </div>
        </div>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Delete lecture"
          onClick={() => setConfirmDelete(true)}
        >
          <Trash2 />
        </Button>
      </header>

      {lecture.error && (
        <p className="mt-4 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
          {lecture.error}
        </p>
      )}

      <div className="mt-4 flex flex-wrap gap-2">
        <Select
          value={lecture.kind || 'none'}
          onValueChange={(v) => update.mutate({ kind: v === 'none' ? '' : v })}
        >
          <SelectTrigger size="sm" className="w-36">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="none">Session type…</SelectItem>
            <SelectItem value="VL">VL (lecture)</SelectItem>
            <SelectItem value="VO">VO (lecture)</SelectItem>
            <SelectItem value="UE">UE (exercise)</SelectItem>
            <SelectItem value="other">Other</SelectItem>
          </SelectContent>
        </Select>
        <Select
          value={String(lecture.subject_id)}
          onValueChange={(v) => update.mutate({ subject_id: Number(v) })}
          disabled={lecture.status === 'recording' || lecture.status === 'processing'}
        >
          <SelectTrigger size="sm" className="w-64">
            <SelectValue />
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

      <section className="mt-6 rounded-lg border bg-card p-4">
        {lecture.has_audio ? (
          <audio ref={audio} controls preload="metadata" className="w-full" src={api.audioUrl(lecture.id)} />
        ) : (
          <p className="text-sm text-muted-foreground">
            {lecture.status === 'recording'
              ? 'Recording in progress. Audio is available after you stop.'
              : lecture.status === 'processing'
                ? 'Encoding audio…'
                : 'No audio.'}
          </p>
        )}
      </section>

      <section className="mt-6">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">Bookmarks</h2>
          {lecture.has_audio && (
            <div className="flex gap-1">
              <Button size="xs" variant="outline" onClick={() => addBookmark.mutate('important')}>
                <Star /> Important here
              </Button>
              <Button size="xs" variant="outline" onClick={() => addBookmark.mutate('confused')}>
                <CircleHelp /> Confused here
              </Button>
            </div>
          )}
        </div>
        {lecture.bookmarks.length === 0 ? (
          <p className="mt-2 text-sm text-muted-foreground">No bookmarks.</p>
        ) : (
          <ul className="mt-2 divide-y rounded-lg border bg-card">
            {lecture.bookmarks.map((b) => {
              const Icon = KIND_ICON[b.kind]
              return (
                <li key={b.id} className="group flex items-center gap-3 px-4 py-2 text-sm">
                  <Icon
                    className={b.kind === 'important' ? 'size-4 text-amber-500' : 'size-4 text-sky-500'}
                  />
                  <button
                    className="font-mono text-primary underline-offset-2 hover:underline disabled:no-underline disabled:opacity-60"
                    disabled={!lecture.has_audio}
                    onClick={() => seek(b.t_s)}
                  >
                    {formatDuration(b.t_s)}
                  </button>
                  <span className="capitalize text-muted-foreground">{b.kind}</span>
                  {b.note && <span className="truncate">{b.note}</span>}
                  <button
                    className="ml-auto opacity-0 transition group-hover:opacity-100"
                    aria-label="Remove bookmark"
                    onClick={() => delBookmark.mutate(b.id)}
                  >
                    <X className="size-4 text-muted-foreground" />
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </section>

      <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete “{lecture.title}”?</AlertDialogTitle>
            <AlertDialogDescription>
              The lecture is removed from LectureLab and its folder is moved to the trash folder in your data
              directory, so the audio can still be recovered by hand.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => del.mutate()}>
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function TitleEditor({ value, onSave }: { value: string; onSave: (v: string) => void }) {
  const [draft, setDraft] = useState(value)
  const commit = () => {
    const v = draft.trim()
    if (v && v !== value) onSave(v)
    else setDraft(value)
  }
  return (
    <Input
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') e.currentTarget.blur()
        if (e.key === 'Escape') {
          setDraft(value)
          e.currentTarget.blur()
        }
      }}
      aria-label="Lecture title"
      className="h-auto border-transparent bg-transparent px-1 text-2xl font-semibold tracking-tight shadow-none hover:border-input focus-visible:border-input md:text-2xl dark:bg-transparent"
    />
  )
}
