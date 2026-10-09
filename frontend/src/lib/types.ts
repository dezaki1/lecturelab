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
  recorder: { mic_default: boolean; slides_default: boolean }
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
  notice: string
  cleanup: { changed: number; model: string; prompt_version: string; at: string } | null
  cleanup_error: string
  cost_usd: number
  bookmark_count: number
  job_progress: number | null
  job_message: string
}

export interface LectureDetail extends Lecture {
  bookmarks: Bookmark[]
  slide_count: number
}

export interface Slide {
  id: number
  t_s: number
  source: 'screen' | 'video' | 'pdf'
  ocr_text: string
  page: number | null
}

export interface LectureDocument {
  id: number
  lecture_id: number | null
  kind: string
  title: string
  created_at: string
  pages: number
  placed: number
  processing: boolean
}

export interface SlideCaptureStatus {
  count: number
  source: string // "" = nothing to capture right now
}

export interface SlideSettings {
  enabled: boolean
  fallback_monitor: number | null
  window_titles: string[]
  interval_s: number
  monitors: { index: number; label: string }[]
}

export type SlideTest =
  | { found: true; source: string; window_title: string; size: [number, number]; preview: string }
  | { found: false; message: string; windows: string[] }

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
  slides?: SlideCaptureStatus | null
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
  raw_text: string | null // Whisper's text when the cleanup pass changed this line
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

export interface SearchHit {
  chunk_id: number
  lecture_id: number | null
  subject_id: number
  subject_name: string
  lecture_number: number | null
  lecture_title: string
  started_at: string | null
  source: 'transcript' | 'note' | 'slide'
  note_kind: string
  heading: string
  snippet: string
  start_s: number | null
  score: number
}

export interface Citation {
  label: string
  lecture_id: number
  start_s: number | null
}

export interface ChatMessage {
  id: number
  role: 'user' | 'assistant'
  content: string
  citations: Citation[]
  model: string
  cost_usd: number
  error: string | null
  created_at: string
}

export interface ChatThread {
  id: number
  scope: 'lecture' | 'subject' | 'all'
  subject_id: number | null
  lecture_id: number | null
  title: string
  grounded: boolean
  date_from: string | null
  date_to: string | null
  updated_at: string
  scope_label: string
  messages: ChatMessage[]
}

export interface ReviewCounts {
  due: number
  new: number
  new_total: number
  next_due: string | null
}

export interface ReviewSummary extends ReviewCounts {
  reviewed_today: number
  streak: number
  by_subject: { subject_id: number; name: string; color: string; due: number; new: number }[]
}

export interface ReviewNext {
  card: Card | null
  remaining: ReviewCounts
  subject_name?: string
  subject_color?: string
  lecture_number?: number | null
  lecture_title?: string
  lecture_id?: number | null
  is_new?: boolean
  reverse?: boolean
  intervals?: Record<string, number>
}

export interface PracticeItem {
  id: number
  attempts: number
  last_score: number | null
  last_max: number | null
  question: QuestionItem
  lecture_id: number | null
  subject_name: string
  subject_color: string
  lecture_number: number | null
  lecture_title: string
}

export interface AttemptResult {
  attempt_id: number
  correct: boolean | null
  score: number
  max_points: number
  auto_graded: boolean
  correct_options: number[]
}

export interface JobRow {
  id: number
  kind: string
  label: string
  detail: string
  lecture_id: number | null
  lecture_title: string | null
  subject_name: string | null
  subject_color: string | null
  status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled'
  progress: number
  message: string
  error: string | null
  attempts: number
  created_at: string
  started_at: string | null
  finished_at: string | null
  duration_s: number | null
}

export interface UsageBucket {
  cost: number
  input: number
  output: number
  cache_read: number
  cache_write: number
  calls: number
}

export interface Usage {
  days: number
  total_cost: number
  all_time_cost: number
  calls: number
  cache_savings: number
  by_task: (UsageBucket & { task: string })[]
  by_model: (UsageBucket & { model: string })[]
  by_day: { day: string; cost: number }[]
  by_subject: { subject_id: number; name: string; color: string; cost: number }[]
  by_lecture: (UsageBucket & {
    lecture_id: number
    title: string
    number: number | null
    subject: string
    duration_s: number | null
  })[]
}
