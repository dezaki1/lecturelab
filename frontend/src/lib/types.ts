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
  remote: boolean // opened from a tablet/phone through LAN mode
}

export interface LanStatus {
  enabled: boolean
  running: boolean
  port: number
  error: string
  urls: string[]
  pair_url: string | null
  qr_svg: string | null
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

export interface HandwrittenPage {
  page: number
  text: string
  unclear: string[]
  summary: string
  edited: boolean
}

export interface HandwrittenNotes {
  id: number
  title: string
  created_at: string
  processing: boolean
  method: 'vision' | 'ocr' | null
  cost_usd: number
  is_pdf: boolean
  pages: HandwrittenPage[]
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

export interface ExamHint {
  id: number
  lecture_id: number
  start_s: number
  end_s: number
  kind: 'exam' | 'important' | 'repeated'
  topic: string
  quote: string
  source: 'rule' | 'llm'
}

export interface SubjectHint extends ExamHint {
  lecture_number: number
  lecture_title: string
}

export interface Explanation {
  id: number
  bookmark_id: number
  title: string
  explanation_md: string
  example_md: string
  check_question: string
  check_answer: string
  source_refs: SourceRef[]
  model: string
  created_at: string
}

export interface HubTopic {
  name: string
  summary: string
  exam_relevant: boolean
  places: { lecture_id: number; number: number; title: string; start_s: number; sections: string[] }[]
  cards: number
  new: number
  due: number
  mastery: number | null
  questions: number
  practice: number | null
  card_ids: number[]
}

export interface ConceptMapData {
  nodes: { id: string; label: string; lecture: number; lecture_id: number | null }[]
  edges: { source: string; target: string; label: string }[]
}

export interface GlossaryEntry {
  term: string
  german: string
  definition: string
  lectures: { lecture_id: number; number: number; title: string; t_s: number | null }[]
}

export interface Hub {
  overview: {
    content_md: string
    version: number
    model: string
    created_at: string
    lectures_covered: number
  } | null
  topics: HubTopic[]
  concept_map: ConceptMapData
  glossary: GlossaryEntry[]
  lectures_with_notes: number
  outdated: boolean
  updating: boolean
  can_update: boolean
}

export interface SubjectDocument extends LectureDocument {
  lecture_number: number | null
  lecture_title: string
}

export type ExamStatus =
  'building' | 'ready' | 'in_progress' | 'grading' | 'graded' | 'failed' | 'grading_failed'

export interface ExamConfig {
  title?: string
  topics: string[]
  types: string[]
  difficulty: 'mixed' | 'easy' | 'medium' | 'hard'
  duration_min?: number
  target_points?: number
  allow_new: boolean
}

export interface ExamSummary {
  id: number
  subject_id: number
  title: string
  status: ExamStatus
  error: string
  config: ExamConfig & { late?: boolean }
  time_limit_s: number
  total_points: number
  score: number | null
  created_at: string
  started_at: string | null
  submitted_at: string | null
  graded_at: string | null
}

export interface ExamQuestion {
  id: number
  type: string
  title: string
  points: number
  prompt: string
  options: { text: string; correct?: boolean; explanation?: string }[]
  answer?: string
  explanation?: string
  rubric?: { criterion: string; points: number }[]
  source_refs?: SourceRef[]
  lecture_id?: number | null
}

export interface ExamAnswer {
  id: number
  position: number
  selected: number[]
  answer_text: string
  score: number | null
  max_points: number
  feedback: string
  rubric_scores: { item: number; points: number; comment: string }[]
  graded_by: string
  question: ExamQuestion
}

export interface ExamDetail extends ExamSummary {
  summary_md: string
  revise: string[]
  deadline: string | null
  server_now: string
  answers: ExamAnswer[]
}

export interface ExamProfile {
  profile: {
    exams: number
    types: Record<string, number>
    topics: { topic: string; share: number }[]
    duration_min: number | null
    total_points: number | null
    avg_points: number | null
    style_notes: string[]
    examples: string[]
  } | null
  past_exams: { id: number; title: string; questions: number; analysed: boolean; has_text: boolean }[]
  bank: Record<string, number>
  can_generate: boolean
}

export interface WeakTopic {
  name: string
  summary: string
  exam_relevant: boolean
  places: { lecture_id: number; number: number; title: string; start_s: number }[]
  mastery: number | null
  strength: number | null
  status: 'weak' | 'ok' | 'untested'
  parts: Record<string, number>
  reviews: number
  forgotten: number
  answers: number
  score: number
  max: number
  cards: number
  questions: number
  forgotten_cards: { id: number; front: string; back: string; times: number }[]
  wrong_answers: {
    question_id: number
    prompt: string
    answer_text: string
    score: number
    max: number
    feedback: string
  }[]
}

export interface PracticeSet {
  id: number
  subject_id: number
  topics: string[]
  question_ids: number[]
  status: 'building' | 'ready' | 'failed'
  error: string
}

export interface CheatSheet {
  note: {
    id: number
    title: string
    content_md: string
    version: number
    edited_by_me: boolean
    pages: number
    model: string
    created_at: string
    updated_at: string
  } | null
  building: boolean
}

export interface BackupResult {
  ok: boolean
  at: string
  error: string
  snapshot?: string
  files_copied: number
  bytes_copied: number
  seconds?: number
}

export interface BackupStatus {
  folder: string
  enabled: boolean
  interval_hours: number
  last: BackupResult | null
  running: boolean
  snapshots: { name: string; bytes: number }[]
  suggested_folder: string
}

export interface Materials {
  notes: Record<string, Note>
  flashcards: Card[]
  exercises: QuestionItem[]
  questions: QuestionItem[]
  hints: ExamHint[]
  explanations: Explanation[]
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
  source: 'transcript' | 'note' | 'slide' | 'handwritten'
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

export type CardState = 'new' | 'learning' | 'review' | 'relearning' | 'suspended'

export interface CardRow extends Card {
  subject_id: number
  lecture_id: number | null
  state: CardState
  reps: number
  lapses: number
  stability: number | null
  due: string | null
  suspended: boolean
  lecture_number: number | null
  lecture_title: string
  subject_name: string
  subject_color: string
  created_at: string
}

export interface CardQuery {
  subject_id?: number
  lecture_id?: number
  state?: string
  q?: string
  sort?: 'lecture' | 'due' | 'newest' | 'lapses'
  offset?: number
  limit?: number
}

export interface SubjectProgress {
  subject_id: number
  name: string
  color: string
  exam_date: string | null
  days_left: number | null
  lectures: number
  lectures_ready: number
  cards: number
  new: number
  young: number
  mature: number
  suspended: number
  due: number
  mastery: number | null
  practice: { attempts: number; score: number; max: number }
}

export interface Dashboard extends ReviewSummary {
  retention: { rate: number | null; reviews: number }
  activity: { date: string; reviews: number; again: number }[]
  forecast: { date: string; due: number }[]
  subjects: SubjectProgress[]
  desired_retention: number
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

export interface TimetableEvent {
  id: number
  summary: string
  location: string
  kind: string
  start: string
  end: string
  course_id: number
  ignored: boolean
  subject_id: number | null
  subject_name: string
  subject_color: string
}

export interface TimetableCourse {
  id: number
  label: string
  subject_id: number | null
  subject_name: string
  mapped_by: string
  ignored: boolean
  events: number
}

export interface TimetableSourceRow {
  id: number
  name: string
  url: string
  is_file: boolean
  last_sync_at: string | null
  error: string
}

export interface TimetableSettings {
  remind_min: number
  auto_record: boolean
  sync_hours: number
}

export interface Timetable {
  sources: TimetableSourceRow[]
  courses: TimetableCourse[]
  settings: TimetableSettings
}

export interface TimetableNow {
  current: TimetableEvent | null
  next: TimetableEvent | null
  today: TimetableEvent[]
}

export type DeadlineKind = 'deadline' | 'exam' | 'assignment' | 'admin' | 'other'

export interface Deadline {
  id: number
  kind: DeadlineKind
  title: string
  due_date: string | null
  due_time: string
  status: 'suggested' | 'open' | 'done' | 'dismissed'
  source: 'manual' | 'lecture'
  subject_id: number | null
  subject_name: string
  subject_color: string
  subject_exam_date: string | null
  lecture_id: number | null
  lecture_title: string
  t_s: number | null
}

export interface DeadlineList {
  suggested: Deadline[]
  open: Deadline[]
  done: Deadline[]
  exams: {
    subject_id: number
    subject_name: string
    subject_color: string
    date: string
    days_left: number
  }[]
}

export interface PlanItem {
  id: number
  date: string
  kind: 'review' | 'topic' | 'practice' | 'mock_exam' | 'cheatsheet' | 'exam' | 'deadline'
  title: string
  minutes: number
  subject_id: number | null
  subject_color: string
  meta: { lecture_id?: number; start_s?: number; strength?: number | null; cards?: number; due_time?: string }
  done: boolean
}

export interface PlannerSettings {
  minutes_per_day: number
  weekdays: number[]
  start_time: string
  block_min: number
}

export interface Plan {
  settings: PlannerSettings
  last_day: string | null
  days: { date: string; study_day: boolean; minutes: number; done_minutes: number; items: PlanItem[] }[]
}

export interface SpeakRef {
  lecture_id: number
  start_s: number
  label: string
  title: string
}

export interface SpeakPoint {
  point: string
  refs: SpeakRef[]
}

export interface SpeakMisconception {
  student_said: string
  correction: string
  refs: SpeakRef[]
}

export interface TeachResult {
  score: number
  verdict: string
  covered: string[]
  missing: SpeakPoint[]
  misconceptions: SpeakMisconception[]
  feedback: string
}

export interface OralResult {
  score: number
  grade: number
  summary: string
  strengths: string[]
  gaps: SpeakPoint[]
  misconceptions: SpeakMisconception[]
  questions: { question: string; score: number; comment: string; refs: SpeakRef[] }[]
  revise: string[]
}

export interface SpeakTurn {
  role: 'examiner' | 'student' | 'coach'
  kind: 'prompt' | 'question' | 'follow_up' | 'end' | 'answer' | 'feedback'
  text: string
  topic?: string
  spoken?: boolean
  rating?: number
  data?: TeachResult
}

export interface SpeakSession {
  id: number
  subject_id: number
  mode: 'teach' | 'oral'
  title: string
  config: { topics: string[]; questions: number; voice: boolean; lecture_ids: number[] }
  turns: SpeakTurn[]
  result: Partial<TeachResult & OralResult>
  score: number | null
  status: 'active' | 'evaluating' | 'done' | 'failed'
  error: string
  cost_usd: number
  created_at: string
  finished_at: string | null
}

export interface SpeakSessionRow {
  id: number
  subject_id: number
  subject_name: string
  mode: 'teach' | 'oral'
  title: string
  status: string
  score: number | null
  grade: number | null
  created_at: string
}

export interface SpeakTopic {
  name: string
  strength: number | null
  status: 'weak' | 'ok' | 'untested'
  exam_relevant: boolean
  lecture_ids: number[]
}

export interface VoiceStatus {
  voice: string
  rate: number
  tts_ready: boolean
  voices: {
    id: string
    label: string
    language: string
    size_mb: number
    installed: boolean
    download: { state: 'running' | 'done' | 'failed'; fraction: number; error: string } | null
  }[]
  stt: { model: string; device: string; loaded: boolean }
}

export interface RecapRef {
  lecture_id: number
  start_s: number
  title: string
  at: string
}

export interface Recap {
  id: number
  scope: 'lecture' | 'week'
  subject_id: number | null
  lecture_id: number | null
  week_start: string | null
  title: string
  minutes: number
  status: 'queued' | 'writing' | 'speaking' | 'ready' | 'failed'
  error: string
  script: { title: string; text: string; refs: RecapRef[] }[]
  chapters: { title: string; start_s: number; refs: RecapRef[] }[]
  duration_s: number
  voice: string
  cost_usd: number
  has_audio: boolean
  created_at: string
  updated_at: string
}
