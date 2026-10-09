import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, CircleHelp, MessageCircleQuestion, MessageSquare, Star, Trash2, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router'
import { toast } from 'sonner'

import { LectureStatusBadge } from '@/components/LectureStatusBadge'
import { SeekContext } from '@/components/Markdown'
import { useStartChat } from '@/pages/ChatPage'
import { GenerationPanel } from '@/components/materials/GenerationPanel'
import { ExerciseList, FlashcardList, QuestionList } from '@/components/materials/ItemLists'
import { NoteView } from '@/components/materials/NoteView'
import { CleanupBar } from '@/components/CleanupBar'
import { SlideDocuments } from '@/components/SlideDocuments'
import { SlidesContext, SlidesGrid, SlideViewer } from '@/components/slides'
import { TranscriptionPanel } from '@/components/TranscriptionPanel'
import { TranscriptView } from '@/components/TranscriptView'
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
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
  const [currentTime, setCurrentTime] = useState(0)
  const [viewer, setViewer] = useState<number | null>(null)
  const [showChanges, setShowChanges] = useState(false)
  const [slideSource, setSlideSource] = useState<'pdf' | 'screen'>('pdf')
  const [params, setParams] = useSearchParams()
  const startAt = params.get('t') != null ? Number(params.get('t')) : null
  const startChat = useStartChat()

  const { data: lecture, error } = useQuery({ queryKey: ['lecture', id], queryFn: () => api.lecture(id) })
  const { data: subjects = [] } = useQuery({ queryKey: ['subjects'], queryFn: api.subjects })
  const { data: materials } = useQuery({
    queryKey: ['materials', id, lecture?.stages.generate, lecture?.job_progress],
    queryFn: () => api.materials(id),
    enabled: lecture?.stages.transcribe === 'done',
  })
  const transcribed = lecture?.stages.transcribe === 'done'
  const { data: transcript } = useQuery({
    queryKey: ['transcript', id, lecture?.transcription?.elapsed_s],
    queryFn: () => api.transcript(id),
    enabled: transcribed,
  })
  const { data: slides = [] } = useQuery({
    queryKey: ['slides', id, lecture?.slide_count, lecture?.stages.slides],
    queryFn: () => api.slides(id),
    enabled: Boolean(lecture?.slide_count),
  })
  // With both a slide PDF and captured slides, one set is shown everywhere (PDF by default).
  const hasPdf = slides.some((s) => s.source === 'pdf')
  const hasCaptured = slides.some((s) => s.source !== 'pdf')
  const source = hasPdf && hasCaptured ? slideSource : hasPdf ? 'pdf' : 'screen'
  const shown = slides.filter((s) => (s.source === 'pdf') === (source === 'pdf'))

  // Keyboard: Space play/pause, ←/→ seek 5 s (when not typing).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = audio.current
      const tag = (e.target as HTMLElement).tagName
      if (!el || ['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON'].includes(tag) || e.ctrlKey || e.metaKey) return
      if (e.key === ' ') {
        e.preventDefault()
        if (el.paused) void el.play()
        else el.pause()
      } else if (e.key === 'ArrowLeft') el.currentTime = Math.max(0, el.currentTime - 5)
      else if (e.key === 'ArrowRight') el.currentTime += 5
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

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
  const revertLine = useMutation({
    mutationFn: (segmentId: number) => api.revertCleanup(id, [segmentId]),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['transcript', id] }),
    onError: (e: Error) => toast.error(e.message),
  })
  const delBookmark = useMutation({
    mutationFn: (bid: number) => api.deleteBookmark(id, bid),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['lecture', id] }),
  })

  if (error) return <div className="p-10 text-sm text-destructive">{error.message}</div>
  if (!lecture) return null
  const subject = subjects.find((s) => s.id === lecture.subject_id)

  const hasNotes = Boolean(materials && Object.keys(materials.notes).length)
  const count = (xs?: unknown[]) => (xs && xs.length ? ` (${xs.length})` : '')

  const seek = (t: number, preRoll: number) => {
    const el = audio.current
    if (!el) return
    el.currentTime = Math.max(0, t - preRoll)
    void el.play()
  }

  return (
    <div className="mx-auto max-w-4xl px-8 py-8" style={{ '--player-h': '4rem' } as React.CSSProperties}>
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
          variant="outline"
          size="sm"
          disabled={lecture.stages.transcribe !== 'done' || startChat.isPending}
          onClick={() => startChat.mutate({ scope: 'lecture', lecture_id: lecture.id })}
        >
          <MessageSquare /> Ask
        </Button>
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

      <div className="sticky top-0 z-20 -mx-8 mt-4 border-b bg-background/95 px-8 py-3 backdrop-blur">
        {lecture.has_audio ? (
          <audio
            ref={audio}
            controls
            preload="metadata"
            className="h-10 w-full"
            src={api.audioUrl(lecture.id)}
            onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime)}
            onLoadedMetadata={(e) => {
              // Opened from a search result or chat citation: jump to that moment.
              if (startAt != null) {
                e.currentTarget.currentTime = startAt
                setCurrentTime(startAt)
                setParams({}, { replace: true })
              }
            }}
            onSeeked={(e) => setCurrentTime(e.currentTarget.currentTime)}
          />
        ) : (
          <p className="text-sm text-muted-foreground">
            {lecture.status === 'recording'
              ? 'Recording in progress. Audio is available after you stop.'
              : lecture.status === 'processing'
                ? 'Encoding audio…'
                : 'No audio.'}
          </p>
        )}
      </div>

      <TranscriptionPanel lecture={lecture} />
      <GenerationPanel lecture={lecture} />

      <SeekContext.Provider value={(t) => seek(t, 0)}>
        <SlidesContext.Provider value={{ slides: shown, open: setViewer }}>
          <Tabs
            key={hasNotes ? 'material' : 'plain'}
            defaultValue={startAt != null || !hasNotes ? 'transcript' : 'summary'}
            className="mt-4"
          >
            <TabsList className="w-full justify-start overflow-x-auto [scrollbar-width:none] [&>button]:flex-none">
              <TabsTrigger value="summary">Summary</TabsTrigger>
              <TabsTrigger value="notes">Notes</TabsTrigger>
              <TabsTrigger value="transcript">Transcript</TabsTrigger>
              <TabsTrigger value="slides">Slides{shown.length ? ` (${shown.length})` : ''}</TabsTrigger>
              <TabsTrigger value="flashcards">Flashcards{count(materials?.flashcards)}</TabsTrigger>
              <TabsTrigger value="exercises">Exercises{count(materials?.exercises)}</TabsTrigger>
              <TabsTrigger value="questions">Exam questions{count(materials?.questions)}</TabsTrigger>
              <TabsTrigger value="glossary">Glossary</TabsTrigger>
              <TabsTrigger value="announcements">Announcements</TabsTrigger>
              <TabsTrigger value="bookmarks">Bookmarks ({lecture.bookmarks.length})</TabsTrigger>
            </TabsList>

            {(['summary', 'notes', 'glossary', 'announcements'] as const).map((kind) => (
              <TabsContent key={kind} value={kind} className="mt-4">
                {materials ? (
                  <NoteView lectureId={lecture.id} kind={kind} note={materials.notes[kind]} />
                ) : (
                  <p className="py-6 text-sm text-muted-foreground">Available after transcription.</p>
                )}
              </TabsContent>
            ))}
            <TabsContent value="flashcards" className="mt-4">
              {materials && <FlashcardList lectureId={lecture.id} cards={materials.flashcards} />}
            </TabsContent>
            <TabsContent value="exercises" className="mt-4">
              {materials && <ExerciseList lectureId={lecture.id} items={materials.exercises} />}
            </TabsContent>
            <TabsContent value="questions" className="mt-4">
              {materials && <QuestionList lectureId={lecture.id} items={materials.questions} />}
            </TabsContent>

            <TabsContent value="transcript" className="mt-2">
              {transcript?.segments.length ? (
                <>
                  <CleanupBar
                    lecture={lecture}
                    corrected={transcript.segments.filter((s) => s.raw_text).length}
                    showChanges={showChanges}
                    onShowChanges={setShowChanges}
                  />
                  <TranscriptView
                    segments={transcript.segments}
                    bookmarks={lecture.bookmarks}
                    slides={shown}
                    currentTime={currentTime}
                    onSeek={(t) => seek(t, 0)}
                    onOpenSlide={setViewer}
                    showChanges={showChanges}
                    onRevert={(sid) => revertLine.mutate(sid)}
                  />
                </>
              ) : (
                <p className="py-6 text-sm text-muted-foreground">
                  {lecture.stages.transcribe === 'running' || lecture.stages.transcribe === 'pending'
                    ? 'The transcript will appear here when transcription has finished.'
                    : 'No transcript yet.'}
                </p>
              )}
            </TabsContent>

            <TabsContent value="slides" className="mt-4">
              <SlideDocuments lecture={lecture} />
              {hasPdf && hasCaptured && (
                <Tabs
                  value={source}
                  onValueChange={(v) => setSlideSource(v as 'pdf' | 'screen')}
                  className="mb-3"
                >
                  <TabsList>
                    <TabsTrigger value="pdf">From the PDF</TabsTrigger>
                    <TabsTrigger value="screen">Captured from the screen</TabsTrigger>
                  </TabsList>
                </Tabs>
              )}
              <SlidesGrid
                lectureId={lecture.id}
                slides={shown}
                stage={lecture.stages.slides}
                onOpen={setViewer}
              />
            </TabsContent>

            <TabsContent value="bookmarks" className="mt-2">
              {lecture.has_audio && (
                <div className="mb-2 flex gap-1">
                  <Button size="xs" variant="outline" onClick={() => addBookmark.mutate('important')}>
                    <Star /> Important here
                  </Button>
                  <Button size="xs" variant="outline" onClick={() => addBookmark.mutate('confused')}>
                    <CircleHelp /> Confused here
                  </Button>
                </div>
              )}
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
                          onClick={() => seek(b.t_s, PRE_ROLL_S)}
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
            </TabsContent>
          </Tabs>
        </SlidesContext.Provider>
      </SeekContext.Provider>

      <SlideViewer
        lectureId={lecture.id}
        slides={shown}
        index={viewer}
        onIndex={setViewer}
        onSeek={(t) => seek(t, 0)}
      />

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
