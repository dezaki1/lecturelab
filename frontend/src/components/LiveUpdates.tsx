import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { useEvent } from '@/hooks/useEvents'

/** Turns server events into cache invalidations and toasts. Renders nothing. */
export function LiveUpdates() {
  const qc = useQueryClient()

  useEvent('subjects', () => qc.invalidateQueries({ queryKey: ['subjects'] }))
  useEvent('lecture', (d: { id: number; subject_id: number }) => {
    qc.invalidateQueries({ queryKey: ['lectures', d.subject_id] })
    qc.invalidateQueries({ queryKey: ['lecture', d.id] })
    qc.invalidateQueries({ queryKey: ['transcript', d.id] })
    qc.invalidateQueries({ queryKey: ['materials', d.id] })
    qc.invalidateQueries({ queryKey: ['subject-hints', d.subject_id] })
    qc.invalidateQueries({ queryKey: ['review-summary'] })
    qc.invalidateQueries({ queryKey: ['subjects'] })
  })
  useEvent('subject_hub', (d: { subject_id: number }) =>
    qc.invalidateQueries({ queryKey: ['hub', d.subject_id] }),
  )
  useEvent('subject_documents', (d: { subject_id: number }) => {
    qc.invalidateQueries({ queryKey: ['subject-documents', d.subject_id] })
    qc.invalidateQueries({ queryKey: ['exam-options', d.subject_id] })
  })
  useEvent('exam', (d: { id: number }) => {
    qc.invalidateQueries({ queryKey: ['exam', d.id] })
    qc.invalidateQueries({ queryKey: ['exams'] })
  })
  useEvent('settings', () => qc.invalidateQueries({ queryKey: ['llm-settings'] }))
  useEvent('review', () => qc.invalidateQueries({ queryKey: ['review-summary'] }))
  useEvent('job', (d: { lecture_id: number | null }) => {
    qc.invalidateQueries({ queryKey: ['job-counts'] })
    qc.invalidateQueries({ queryKey: ['hub'] }) // "updating" state of course overviews
    if (d.lecture_id == null) return
    qc.invalidateQueries({ queryKey: ['lecture', d.lecture_id] })
    qc.invalidateQueries({ queryKey: ['lectures'] })
  })
  useEvent('recorder.slides', (d: { lecture_id: number }) => {
    qc.invalidateQueries({ queryKey: ['lecture', d.lecture_id] })
  })
  useEvent('recorder.bookmark', (d: { lecture_id: number }) => {
    qc.invalidateQueries({ queryKey: ['lecture', d.lecture_id] })
  })
  useEvent('notify', (d: { title: string; message: string; level: string }) => {
    // Bookmark confirmations already show in the recorder panel; keep the UI quiet for those.
    if (d.title === 'Bookmark') return toast(d.message, { duration: 1500 })
    const fn =
      d.level === 'error'
        ? toast.error
        : d.level === 'warning'
          ? toast.warning
          : d.level === 'success'
            ? toast.success
            : toast.info
    fn(d.title, { description: d.message })
  })

  return null
}
