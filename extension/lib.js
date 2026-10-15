// Shared by the background service worker and the popup.

export const DEFAULT_SERVER = 'http://127.0.0.1:8765'

export async function serverUrl() {
  const { server } = await chrome.storage.local.get('server')
  return (server || DEFAULT_SERVER).replace(/\/+$/, '')
}

async function call(method, path, body) {
  const base = await serverUrl()
  let res
  try {
    res = await fetch(base + path, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    })
  } catch {
    throw new Error(`LectureLab isn't running at ${base}. Start it on this PC and try again.`)
  }
  if (!res.ok) {
    let msg = res.statusText
    try {
      const data = await res.json()
      if (typeof data.detail === 'string') msg = data.detail
      else if (Array.isArray(data.detail)) msg = data.detail.map((d) => d.msg).join('; ')
    } catch {
      /* not JSON */
    }
    throw new Error(msg || `HTTP ${res.status}`)
  }
  return res.status === 204 ? null : res.json()
}

export const lecturelab = {
  subjects: () => call('GET', '/api/subjects'),
  recorder: () => call('GET', '/api/recorder'),
  send: (data) => call('POST', '/api/downloads', data),
  save: (data) => call('POST', '/api/downloads/save', data),
  openFolder: () => call('POST', '/api/downloads/open-folder'),
}

/** The subject used last from the extension, else the recorder's last subject, else the first. */
export async function defaultSubject(subjects) {
  const { subjectId } = await chrome.storage.local.get('subjectId')
  if (subjects.some((s) => s.id === subjectId)) return subjectId
  try {
    const rec = await lecturelab.recorder()
    if (subjects.some((s) => s.id === rec.last_subject_id)) return rec.last_subject_id
  } catch {
    /* fine */
  }
  return subjects[0]?.id
}

/** The browser's cookies for these URLs in Netscape cookies.txt format (for yt-dlp). */
export async function cookiesFor(urls) {
  const seen = new Map()
  for (const url of urls) {
    if (!/^https?:/i.test(url || '')) continue
    let cookies = []
    try {
      cookies = await chrome.cookies.getAll({ url })
    } catch {
      continue
    }
    for (const c of cookies) seen.set(`${c.domain}|${c.path}|${c.name}`, c)
  }
  const lines = ['# Netscape HTTP Cookie File']
  for (const c of seen.values()) {
    const domain = c.hostOnly
      ? c.domain.replace(/^\./, '')
      : c.domain.startsWith('.')
        ? c.domain
        : '.' + c.domain
    lines.push(
      [
        domain,
        c.hostOnly ? 'FALSE' : 'TRUE',
        c.path,
        c.secure ? 'TRUE' : 'FALSE',
        Math.floor(c.expirationDate || 0),
        c.name,
        c.value,
      ].join('\t'),
    )
  }
  return seen.size ? lines.join('\n') + '\n' : ''
}

/**
 * Send a page or media link to LectureLab.
 * item: { url, referer?, pageUrl? }; opts: { subjectId, kind, title, video, sendCookies }
 */
export async function sendToLectureLab(item, opts) {
  const cookies = opts.sendCookies ? await cookiesFor([item.url, item.pageUrl, item.referer]) : ''
  return lecturelab.send({
    url: item.url,
    subject_id: opts.subjectId,
    title: opts.title || '',
    kind: opts.kind || '',
    video: opts.video,
    referer: item.referer || '',
    user_agent: navigator.userAgent,
    cookies,
  })
}

/** Just download: LectureLab saves the file to the Downloads folder, no lecture. */
export async function saveToDisk(item, opts) {
  const cookies = opts.sendCookies ? await cookiesFor([item.url, item.pageUrl, item.referer]) : ''
  return lecturelab.save({
    url: item.url,
    video: opts.video,
    title: item.fileTitle || '',
    referer: item.referer || '',
    user_agent: navigator.userAgent,
    cookies,
  })
}

export async function lectureUrl(id) {
  return `${await serverUrl()}/lectures/${id}`
}
