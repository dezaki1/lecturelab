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
  language: string | null
  transcription: TranscriptionStats | null
  cost_usd: number
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

export interface TranscriptionStats {
  model: string
  device: 'cuda' | 'cpu'
  compute_type: string
  elapsed_s: number
  load_s: number
  speed_x: number | null
  language_probability: number
  segments: number
  gpu_fallback_reason: string | null
}

export interface Segment {
  id: number
  start: number
  end: number
  text: string
  confidence: number | null
}

export interface Transcript {
  lecture_id: number
  language: string | null
  segments: Segment[]
}

export interface SourceRef {
  type: 'lecture' | 'material'
  lecture_id?: number
  start_s?: number
  end_s?: number
  material_id?: number
  page?: number | null
}

export interface NoteVersion {
  id: number
  version: number
  created_at: string
  edited_by_me: boolean
  model: string
  instructions: string
}

export interface Note {
  id: number
  kind: string
  version: number
  is_current: boolean
  content_md: string
  data: Record<string, any>
  source_refs: SourceRef[]
  edited_by_me: boolean
  model: string
  prompt_version: string
  created_at: string
  updated_at: string
  versions: NoteVersion[]
}

export interface Card {
  id: number
  type: 'basic' | 'cloze' | 'reversed'
  front: string
  back: string
  tags: string[]
  difficulty: string
  source_refs: SourceRef[]
  verified: 'unchecked' | 'ok' | 'flagged'
  verify_note: string
  edited_by_me: boolean
}

export interface QuestionItem {
  id: number
  kind: 'exam' | 'exercise'
  type: string
  title: string
  bloom: string
  difficulty: string
  points: number
  prompt: string
  options: { text: string; correct: boolean; explanation: string }[]
  answer: string
  explanation: string
  rubric: { criterion: string; points: number }[]
  hints: string[]
  solution_steps: string[]
  expected_output: string
  emphasized: boolean
  source_refs: SourceRef[]
  verified: 'unchecked' | 'ok' | 'flagged'
  verify_note: string
}

export interface Materials {
  notes: Record<string, Note>
  flashcards: Card[]
  exercises: QuestionItem[]
  questions: QuestionItem[]
}

export interface LLMSettings {
  provider: string
  providers: string[]
  anthropic: {
    main: string
    cheap: string
    main_effort: string
    key: { set: boolean; hint: string | null; source: string | null }
  }
  ollama: { url: string; model: string }
  openai: { base_url: string; model: string }
}

export interface Estimate {
  provider: string
  model?: string
  usd: number
  context_tokens: number
  configured: boolean
}
