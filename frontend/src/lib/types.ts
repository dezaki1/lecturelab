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
