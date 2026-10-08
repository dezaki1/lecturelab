import type {
  Bookmark,
  BookmarkKind,
  Estimate,
  LLMSettings,
  Materials,
  Note,
  Lecture,
  LectureDetail,
  RecorderStatus,
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
  retranscribe: (lectureId: number, language?: string) =>
    request<LectureDetail>('POST', `/api/lectures/${lectureId}/transcribe`, language ? { language } : {}),

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

  recorder: () => request<RecorderStatus>('GET', '/api/recorder'),
  startRecording: (data: { subject_id: number; mic: boolean }) =>
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
