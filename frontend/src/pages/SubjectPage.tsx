import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CalendarDays, Pencil, Trash2, Upload, User } from 'lucide-react'
import { useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import { toast } from 'sonner'

import { IMPORT_ACCEPT, useImport } from '@/components/ImportDialog'
import { LectureList } from '@/components/LectureList'
import { SubjectDialog } from '@/components/SubjectDialog'
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
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import { daysUntil, formatDate } from '@/lib/dates'

const LANG_LABEL = { auto: 'Auto-detect', en: 'English', de: 'German' } as const

export function SubjectPage() {
  const id = Number(useParams().id)
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [editing, setEditing] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const { openImport } = useImport()
  const fileInput = useRef<HTMLInputElement>(null)

  const { data: subject, error } = useQuery({
    queryKey: ['subject', id],
    queryFn: () => api.subject(id),
  })

  const del = useMutation({
    mutationFn: () => api.deleteSubject(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['subjects'] })
      toast.success('Subject deleted')
      navigate('/')
    },
    onError: (e: Error) => toast.error(e.message),
  })

  if (error) return <div className="p-10 text-sm text-destructive">{error.message}</div>
  if (!subject) return null
  const days = subject.exam_date ? daysUntil(subject.exam_date) : null

  return (
    <div className="mx-auto max-w-5xl px-8 py-10">
      <header className="flex items-start gap-4">
        <span className="mt-2 size-3.5 shrink-0 rounded-full" style={{ background: subject.color }} />
        <div className="min-w-0 flex-1">
          <h1 className="text-2xl font-semibold tracking-tight">{subject.name}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted-foreground">
            {subject.course_code && <span>{subject.course_code}</span>}
            {subject.lecturer && (
              <span className="flex items-center gap-1">
                <User className="size-3.5" /> {subject.lecturer}
              </span>
            )}
            {subject.exam_date && (
              <span className="flex items-center gap-1">
                <CalendarDays className="size-3.5" /> Exam {formatDate(subject.exam_date)}
                {days !== null && days >= 0 && <Badge variant="secondary">{days} days</Badge>}
              </span>
            )}
            <span>Spoken: {LANG_LABEL[subject.language]}</span>
          </div>
        </div>
        <div className="flex gap-1">
          <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
            <Pencil /> Edit
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Delete subject"
            onClick={() => setConfirmDelete(true)}
          >
            <Trash2 />
          </Button>
        </div>
      </header>

      {(subject.instructions || subject.vocabulary.length > 0) && (
        <section className="mt-6 space-y-3 rounded-lg border bg-card p-4 text-sm">
          {subject.instructions && (
            <p>
              <span className="text-muted-foreground">Instructions: </span>
              {subject.instructions}
            </p>
          )}
          {subject.vocabulary.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {subject.vocabulary.map((t) => (
                <Badge key={t} variant="outline">
                  {t}
                </Badge>
              ))}
            </div>
          )}
        </section>
      )}

      <section className="mt-8">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold">Lectures</h2>
          <Button variant="outline" size="sm" onClick={() => fileInput.current?.click()}>
            <Upload /> Import
          </Button>
          <input
            ref={fileInput}
            type="file"
            multiple
            accept={IMPORT_ACCEPT}
            className="hidden"
            onChange={(e) => {
              openImport(Array.from(e.target.files ?? []), subject.id)
              e.target.value = ''
            }}
          />
        </div>
        <LectureList subjectId={subject.id} />
      </section>

      <SubjectDialog open={editing} onOpenChange={setEditing} subject={subject} />
      <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {subject.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              Subjects that still have lectures can't be deleted.
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
