import type {
  Bookmark,
  BookmarkKind,
  AttemptResult,
  ChatMessage,
  ChatThread,
  JobRow,
  Usage,
  PracticeItem,
  ReviewNext,
  ReviewSummary,
  SearchHit,
  Estimate,
  LLMSettings,
  Materials,
  Note,
  Lecture,
  LectureDetail,
  LectureDocument,
  RecorderStatus,
  SubjectHint,
  Card,
  CardQuery,
  CardRow,
  Dashboard,
  Slide,
  SlideSettings,
  SlideTest,
  Subject,
  SubjectInput,
  SystemInfo,
  Transcript,
} from './types'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

/** Query string from defined, non-empty values. */
function qs(p: object): string {
  const out = new URLSearchParams()
  for (const [k, v] of Object.entries(p as Record<string, unknown>))
    if (v !== undefined && v !== null && v !== '') out.set(k, String(v))
  return out.toString()
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) {
    let msg = res.statusText
    try {
      const data = await res.json()
      if (typeof data.detail === 'string') msg = data.detail
      else if (Array.isArray(data.detail)) msg = data.detail.map((d: { msg: string }) => d.msg).join('; ')
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, msg)
  }
  return (res.status === 204 ? undefined : await res.json()) as T
}

export const api = {
  get: <T>(url: string) => request<T>('GET', url),
  post: <T>(url: string, body?: unknown) => request<T>('POST', url, body ?? {}),
  patch: <T>(url: string, body: unknown) => request<T>('PATCH', url, body),
  del: (url: string) => request<void>('DELETE', url),

  system: () => request<SystemInfo>('GET', '/api/system'),
  subjects: () => request<Subject[]>('GET', '/api/subjects'),
  subject: (id: number) => request<Subject>('GET', `/api/subjects/${id}`),
  createSubject: (data: SubjectInput) => request<Subject>('POST', '/api/subjects', data),
  updateSubject: (id: number, data: Partial<Subject>) =>
    request<Subject>('PATCH', `/api/subjects/${id}`, data),
  deleteSubject: (id: number) => request<void>('DELETE', `/api/subjects/${id}`),

  lectures: (subjectId: number) => request<Lecture[]>('GET', `/api/lectures?subject_id=${subjectId}`),
  lecture: (id: number) => request<LectureDetail>('GET', `/api/lectures/${id}`),
  updateLecture: (id: number, data: Partial<Pick<Lecture, 'title' | 'kind' | 'subject_id'>>) =>
    request<LectureDetail>('PATCH', `/api/lectures/${id}`, data),
  deleteLecture: (id: number) => request<void>('DELETE', `/api/lectures/${id}`),
  addBookmark: (lectureId: number, data: { t_s: number; kind: BookmarkKind; note?: string }) =>
    request<Bookmark>('POST', `/api/lectures/${lectureId}/bookmarks`, data),
  deleteBookmark: (lectureId: number, id: number) =>
    request<void>('DELETE', `/api/lectures/${lectureId}/bookmarks/${id}`),
  audioUrl: (lectureId: number) => `/api/lectures/${lectureId}/audio`,
  transcript: (lectureId: number) => request<Transcript>('GET', `/api/lectures/${lectureId}/transcript`),
  slides: (lectureId: number) => request<Slide[]>('GET', `/api/lectures/${lectureId}/slides`),
  slideImageUrl: (slideId: number) => `/api/slides/${slideId}/image`,
  deleteSlide: (slideId: number) => request<void>('DELETE', `/api/slides/${slideId}`),
  rerunSlideOcr: (lectureId: number) =>
    request<{ queued: boolean }>('POST', `/api/lectures/${lectureId}/slides/ocr`),
  documents: (lectureId: number) => request<LectureDocument[]>('GET', `/api/lectures/${lectureId}/documents`),
  documentFileUrl: (id: number) => `/api/documents/${id}/file`,
  deleteDocument: (id: number) => request<void>('DELETE', `/api/documents/${id}`),
  placeDocument: (id: number) => request<LectureDocument>('POST', `/api/documents/${id}/place`),
  runCleanup: (lectureId: number) => request<LectureDetail>('POST', `/api/lectures/${lectureId}/cleanup`),
  revertCleanup: (lectureId: number, segmentIds?: number[]) =>
    request<{ reverted: number }>('POST', `/api/lectures/${lectureId}/cleanup/revert`, {
      segment_ids: segmentIds ?? null,
    }),
  slideSettings: () => request<SlideSettings>('GET', '/api/settings/slides'),
  putSlideSettings: (data: Partial<Pick<SlideSettings, 'enabled' | 'fallback_monitor' | 'window_titles'>>) =>
    request<SlideSettings>('PUT', '/api/settings/slides', data),
  testSlideCapture: () => request<SlideTest>('POST', '/api/settings/slides/test'),
  retranscribe: (lectureId: number, language?: string) =>
    request<LectureDetail>('POST', `/api/lectures/${lectureId}/transcribe`, language ? { language } : {}),

  subjectHints: (subjectId: number) => request<SubjectHint[]>('GET', `/api/subjects/${subjectId}/hints`),
  explanationToCard: (id: number) => request<Card>('POST', `/api/explanations/${id}/flashcard`),
  materials: (lectureId: number) => request<Materials>('GET', `/api/lectures/${lectureId}/materials`),
  estimate: (lectureId: number) => request<Estimate>('GET', `/api/lectures/${lectureId}/generate/estimate`),
  generate: (lectureId: number, kinds?: string[], instructions = '') =>
    request<{ job_id: number }>('POST', `/api/lectures/${lectureId}/generate`, { kinds, instructions }),
  note: (id: number) => request<Note>('GET', `/api/notes/${id}`),
  editNote: (id: number, content_md: string) => request<Note>('PATCH', `/api/notes/${id}`, { content_md }),
  restoreNote: (id: number) => request<Note>('POST', `/api/notes/${id}/restore`),
  deleteCard: (id: number) => request<void>('DELETE', `/api/flashcards/${id}`),
  deleteQuestion: (id: number) => request<void>('DELETE', `/api/questions/${id}`),

  llmSettings: () => request<LLMSettings>('GET', '/api/settings/llm'),
  setProvider: (provider: string) => request<LLMSettings>('PUT', '/api/settings/llm/provider', { provider }),
  setApiKey: (key: string, provider = 'anthropic') =>
    request<{ set: boolean; hint: string | null }>('PUT', '/api/settings/api-key', { provider, key }),
  deleteApiKey: (provider = 'anthropic') => request<unknown>('DELETE', `/api/settings/api-key/${provider}`),
  testLlm: () => request<{ ok: boolean; error?: string; model?: string }>('POST', '/api/settings/llm/test'),

  search: (q: string, subjectId?: number) =>
    request<SearchHit[]>(
      'GET',
      `/api/search?${new URLSearchParams({ q, ...(subjectId ? { subject_id: String(subjectId) } : {}) })}`,
    ),
  threads: (params: { subject_id?: number; lecture_id?: number } = {}) =>
    request<ChatThread[]>(
      'GET',
      `/api/chat/threads?${new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)]))}`,
    ),
  thread: (id: number) => request<ChatThread>('GET', `/api/chat/threads/${id}`),
  createThread: (data: {
    scope: 'lecture' | 'subject' | 'all'
    subject_id?: number
    lecture_id?: number
    grounded?: boolean
    date_from?: string | null
    date_to?: string | null
  }) => request<ChatThread>('POST', '/api/chat/threads', data),
  updateThread: (
    id: number,
    data: Partial<Pick<ChatThread, 'title' | 'grounded' | 'date_from' | 'date_to'>>,
  ) => request<ChatThread>('PATCH', `/api/chat/threads/${id}`, data),
  deleteThread: (id: number) => request<void>('DELETE', `/api/chat/threads/${id}`),
  flashcardsFromMessage: (id: number) =>
    request<{ created: number }>('POST', `/api/chat/messages/${id}/flashcards`),

  reviewSummary: () => request<ReviewSummary>('GET', '/api/review/summary'),
  reviewNext: (subjectId?: number) =>
    request<ReviewNext>('GET', `/api/review/next${subjectId ? `?subject_id=${subjectId}` : ''}`),
  reviewCard: (id: number, rating: number, duration_ms?: number) =>
    request<unknown>('POST', `/api/review/${id}`, { rating, duration_ms }),
  reviewUndo: () => request<{ card_id: number }>('POST', '/api/review/undo'),
  reviewExtraNew: (count = 10) => request<unknown>('POST', '/api/review/extra-new', { count }),
  updateCard: (
    id: number,
    data: { front?: string; back?: string; tags?: string[]; type?: string; suspended?: boolean },
  ) => request<unknown>('PATCH', `/api/flashcards/${id}`, data),
  cards: (p: CardQuery) => request<{ total: number; items: CardRow[] }>('GET', `/api/cards?${qs(p)}`),
  bulkCards: (ids: number[], action: 'suspend' | 'unsuspend' | 'delete' | 'forget') =>
    request<{ changed: number }>('POST', '/api/cards/bulk', { ids, action }),
  duplicates: (subjectId?: number) =>
    request<CardRow[][]>('GET', `/api/cards/duplicates?${qs({ subject_id: subjectId })}`),
  mergeCards: (keepId: number, mergeIds: number[]) =>
    request<CardRow>('POST', '/api/cards/merge', { keep_id: keepId, merge_ids: mergeIds }),
  exportCardsUrl: (format: 'apkg' | 'csv', p: CardQuery & { ids?: number[] }) => {
    const { ids, ...rest } = p
    const params = new URLSearchParams(qs({ format, ...rest }))
    ids?.forEach((id) => params.append('ids', String(id)))
    return `/api/cards/export?${params}`
  },
  dashboard: () => request<Dashboard>('GET', '/api/dashboard'),
  practiceQuestions: (params: Record<string, string | number | undefined>) =>
    request<PracticeItem[]>(
      'GET',
      `/api/practice/questions?${new URLSearchParams(
        Object.entries(params)
          .filter(([, v]) => v !== undefined && v !== '')
          .map(([k, v]) => [k, String(v)]),
      )}`,
    ),
  submitAttempt: (
    questionId: number,
    data: {
      selected?: number[]
      answer_text?: string
      rubric_checked?: number[]
      self_score?: number
      duration_ms?: number
    },
  ) => request<AttemptResult>('POST', `/api/practice/questions/${questionId}/attempts`, data),

  jobs: (status = 'all') => request<JobRow[]>('GET', `/api/jobs?status=${status}`),
  jobCounts: () =>
    request<{ queued: number; running: number; failed_recent: number }>('GET', '/api/jobs/counts'),
  retryJob: (id: number) => request<unknown>('POST', `/api/jobs/${id}/retry`),
  cancelJob: (id: number) => request<unknown>('POST', `/api/jobs/${id}/cancel`),
  usage: (days = 30) => request<Usage>('GET', `/api/usage?days=${days}`),
  pendingGeneration: () =>
    request<{ count: number; usd: number; configured: boolean; lectures: { id: number; title: string }[] }>(
      'GET',
      '/api/generate/pending',
    ),
  generatePending: () => request<{ queued: number }>('POST', '/api/generate/pending'),

  recorder: () => request<RecorderStatus>('GET', '/api/recorder'),
  startRecording: (data: { subject_id: number; mic: boolean; slides: boolean }) =>
    request<RecorderStatus>('POST', '/api/recorder/start', data),
  stopRecording: () => request<{ lecture_id: number; duration_s: number }>('POST', '/api/recorder/stop'),
  bookmark: (kind: BookmarkKind) => request<Bookmark>('POST', '/api/recorder/bookmark', { kind }),
}

/** Stream a file to the import endpoint (raw body), reporting upload progress 0..1. */
export function uploadImport(
  file: File,
  opts: { subject_id: number; title?: string; kind?: string },
  onProgress: (fraction: number) => void,
): Promise<Lecture> {
  const params = new URLSearchParams({
    subject_id: String(opts.subject_id),
    filename: file.name,
    last_modified_ms: String(file.lastModified),
  })
  if (opts.title) params.set('title', opts.title)
  if (opts.kind) params.set('kind', opts.kind)
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `/api/imports?${params}`)
    xhr.setRequestHeader('Content-Type', 'application/octet-stream')
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total)
    xhr.onload = () => {
      let data: { detail?: unknown } = {}
      try {
        data = JSON.parse(xhr.responseText)
      } catch {
        /* not JSON */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data as Lecture)
      else reject(new ApiError(xhr.status, typeof data.detail === 'string' ? data.detail : xhr.statusText))
    }
    xhr.onerror = () => reject(new ApiError(0, 'Upload failed (is LectureLab still running?)'))
    xhr.send(file)
  })
}

/** Upload a PDF to a lecture (raw body). */
export async function uploadDocument(lectureId: number, file: File): Promise<LectureDocument> {
  const params = new URLSearchParams({ filename: file.name, kind: 'slides' })
  const res = await fetch(`/api/lectures/${lectureId}/documents?${params}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/pdf' },
    body: file,
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new ApiError(res.status, typeof data.detail === 'string' ? data.detail : res.statusText)
  return data as LectureDocument
}

export type ChatStreamEvent =
  | { type: 'user'; message: ChatMessage }
  | {
      type: 'sources'
      sources: { label: string; lecture_id: number; start_s: number | null; title: string }[]
    }
  | { type: 'delta'; text: string }
  | { type: 'done'; message: ChatMessage }
  | { type: 'error'; message: string }

/** POST a question and read the Server-Sent Events reply stream. */
export async function askStream(threadId: number, content: string, onEvent: (e: ChatStreamEvent) => void) {
  const res = await fetch(`/api/chat/threads/${threadId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  })
  if (!res.ok || !res.body) throw new ApiError(res.status, `Chat request failed (${res.status})`)
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let sep
    while ((sep = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, sep)
      buffer = buffer.slice(sep + 2)
      const event = /^event: (.+)$/m.exec(block)?.[1]
      const data = /^data: (.*)$/m.exec(block)?.[1]
      if (!event || data == null) continue
      const payload = JSON.parse(data)
      if (event === 'user') onEvent({ type: 'user', message: payload })
      else if (event === 'sources') onEvent({ type: 'sources', sources: payload })
      else if (event === 'delta') onEvent({ type: 'delta', text: payload.text })
      else if (event === 'done') onEvent({ type: 'done', message: payload })
      else if (event === 'error') onEvent({ type: 'error', message: payload.message })
    }
  }
}
