import type {
  Bookmark,
  BookmarkKind,
  Lecture,
  LectureDetail,
  RecorderStatus,
  Subject,
  SubjectInput,
  SystemInfo,
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

  recorder: () => request<RecorderStatus>('GET', '/api/recorder'),
  startRecording: (data: { subject_id: number; mic: boolean }) =>
    request<RecorderStatus>('POST', '/api/recorder/start', data),
  stopRecording: () => request<{ lecture_id: number; duration_s: number }>('POST', '/api/recorder/stop'),
  bookmark: (kind: BookmarkKind) => request<Bookmark>('POST', '/api/recorder/bookmark', { kind }),
}
