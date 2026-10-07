export type Language = 'auto' | 'en' | 'de'

export interface Subject {
  id: number
  name: string
  slug: string
  course_code: string
  language: Language
  output_language: string
  lecturer: string
  color: string
  exam_date: string | null
  instructions: string
  vocabulary: string[]
  recording_notice_ack: boolean
  archived: boolean
  lecture_count: number
  created_at: string
  updated_at: string
}

export type SubjectInput = Pick<
  Subject,
  | 'name'
  | 'course_code'
  | 'language'
  | 'output_language'
  | 'lecturer'
  | 'color'
  | 'exam_date'
  | 'instructions'
  | 'vocabulary'
>

export interface SystemInfo {
  data_dir: string
  free_gb: number
  hotkeys: { start_stop: string; important: string; confused: string; enabled: boolean }
  recorder: { mic_default: boolean }
}

export type BookmarkKind = 'important' | 'confused' | 'question'

export interface Bookmark {
  id: number
  t_s: number
  kind: BookmarkKind
  note: string
}

export type LectureStatus = 'recording' | 'processing' | 'ready' | 'failed'

export interface Lecture {
  id: number
  subject_id: number
  number: number
  title: string
  kind: string
  started_at: string
  duration_s: number | null
  source: 'recording' | 'import'
  status: LectureStatus
  stages: Record<string, string>
  error: string | null
  has_audio: boolean
  bookmark_count: number
  job_progress: number | null
  job_message: string
}

export interface LectureDetail extends Lecture {
  bookmarks: Bookmark[]
}

export interface RecorderStatus {
  recording: boolean
  free_gb: number
  last_subject_id?: number | null
  lecture_id?: number
  subject_id?: number
  subject_name?: string
  title?: string
  mic?: boolean
  elapsed_s?: number
  devices?: Record<string, string>
  bookmarks?: { t_s: number; kind: BookmarkKind; note: string }[]
}

export interface Level {
  rms_db: number
  peak_db: number
  active: boolean
}
