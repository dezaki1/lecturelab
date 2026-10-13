import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { useEvent } from '@/hooks/useEvents'

interface Notify {
  title: string
  message: string
  level: string
  action?: { type: 'record'; subject_id: number; kind: string }
}

/** Turns server events into cache invalidations and toasts. Renders nothing.
 * Not on print pages: the open event stream keeps a headless browser waiting forever. */
export function LiveUpdates() {
  if (window.location.pathname.startsWith('/print/')) return null
  return <Listeners />
}

function Listeners() {
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
  useEvent('practice_set', (d: { id: number }) => qc.invalidateQueries({ queryKey: ['practice-set', d.id] }))
  useEvent('exam', (d: { id: number }) => {
    qc.invalidateQueries({ queryKey: ['exam', d.id] })
    qc.invalidateQueries({ queryKey: ['exams'] })
  })
  useEvent('exams', (d: { subject_id: number }) =>
    qc.invalidateQueries({ queryKey: ['exam-profile', d.subject_id] }),
  )
  useEvent('cheatsheet', (d: { subject_id: number }) =>
    qc.invalidateQueries({ queryKey: ['cheatsheet', d.subject_id] }),
  )
  useEvent('subject_hub', (d: { subject_id: number }) =>
    qc.invalidateQueries({ queryKey: ['hub', d.subject_id] }),
  )
  useEvent('settings', () => qc.invalidateQueries({ queryKey: ['llm-settings'] }))
  useEvent('review', () => {
    qc.invalidateQueries({ queryKey: ['review-summary'] })
    qc.invalidateQueries({ queryKey: ['weak-spots'] })
    qc.invalidateQueries({ queryKey: ['weak-overview'] })
  })
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
  useEvent('deadlines', () => {
    qc.invalidateQueries({ queryKey: ['deadlines'] })
    qc.invalidateQueries({ queryKey: ['plan'] })
  })
  useEvent('planner', () => qc.invalidateQueries({ queryKey: ['plan'] }))
  useEvent('timetable', () => {
    qc.invalidateQueries({ queryKey: ['timetable'] })
    qc.invalidateQueries({ queryKey: ['timetable-now'] })
  })
  useEvent('notify', (d: Notify) => {
    // Bookmark confirmations already show in the recorder panel; keep the UI quiet for those.
    if (d.title === 'Bookmark') return toast(d.message, { duration: 1500 })
    const act = d.action
    if (act?.type === 'record') {
      // Timetable reminder: the recorder panel starts it (and shows the recording notice if needed).
      return toast.info(d.title, {
        description: d.message,
        duration: 60_000,
        action: {
          label: 'Record',
          onClick: () =>
            window.dispatchEvent(new CustomEvent('lecturelab:record', { detail: act.subject_id })),
        },
      })
    }
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
