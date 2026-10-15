// Watches network requests for videos and audio (like Video DownloadHelper) and remembers them
// per tab, so the popup can list them. Also adds "Send to LectureLab" to the right-click menu.

import { defaultSubject, lectureUrl, lecturelab, sendToLectureLab } from './lib.js'

const MEDIA_EXT = /\.(m3u8|mpd|mp4|m4v|webm|mkv|mov|m4a|mp3|aac|ogg|oga|opus|flac|wav)(?:$|[?#;])/i
const MEDIA_TYPE = /^(video|audio)\/|mpegurl|dash\+xml/i
// Pieces of a stream, not something to download on their own.
const SEGMENT =
  /\.(ts|m4s|m4f|cmfv|cmfa|vtt|webvtt|srt|key)(?:$|[?#])|[/_-](seg|segment|frag|fragment|chunk)[-_]?\d+|\/range\/|[?&](range|bytes)=/i
const SEGMENT_TYPE = /mp2t|iso\.segment|vtt/i
const MIN_PROGRESSIVE_BYTES = 300_000 // ignore UI sounds and tiny previews
const MAX_PER_TAB = 30

function header(headers, name) {
  return headers?.find((h) => h.name.toLowerCase() === name)?.value || ''
}

function classify(url, type) {
  if (/\.m3u8(?:$|[?#])/i.test(url) || /mpegurl/i.test(type)) return 'HLS'
  if (/\.mpd(?:$|[?#])/i.test(url) || /dash\+xml/i.test(type)) return 'DASH'
  if (/^audio\//i.test(type) || /\.(m4a|mp3|aac|ogg|oga|opus|flac|wav)(?:$|[?#;])/i.test(url)) return 'Audio'
  return 'Video'
}

async function tabItems(tabId) {
  const key = `tab:${tabId}`
  return (await chrome.storage.session.get(key))[key] || []
}

async function setTabItems(tabId, items) {
  await chrome.storage.session.set({ [`tab:${tabId}`]: items })
  await chrome.action.setBadgeText({ tabId, text: items.length ? String(items.length) : '' })
}

// One queue so concurrent requests don't overwrite each other's storage writes.
let pending = Promise.resolve()
const serial = (fn) => (pending = pending.then(fn, fn))

chrome.webRequest.onHeadersReceived.addListener(
  (d) => {
    if (d.tabId < 0 || d.statusCode >= 400 || d.method !== 'GET') return
    const type = header(d.responseHeaders, 'content-type').split(';')[0].trim()
    if (!MEDIA_EXT.test(d.url) && !MEDIA_TYPE.test(type)) return
    if (SEGMENT.test(d.url) || SEGMENT_TYPE.test(type)) return
    const kind = classify(d.url, type)
    const size = Number(header(d.responseHeaders, 'content-length')) || 0
    const total = Number(header(d.responseHeaders, 'content-range').split('/')[1]) || size
    const isStream = kind === 'HLS' || kind === 'DASH'
    if (!isStream && total && total < MIN_PROGRESSIVE_BYTES) return
    const referer = d.documentUrl || d.frameAncestors?.[0]?.url || (d.initiator ? d.initiator + '/' : '')
    serial(async () => {
      const items = await tabItems(d.tabId)
      const existing = items.find((i) => i.url === d.url)
      if (existing) {
        existing.size = Math.max(existing.size || 0, total)
        await setTabItems(d.tabId, items)
        return
      }
      items.unshift({ url: d.url, kind, type, size: total, referer, seen: Date.now() })
      await setTabItems(d.tabId, items.slice(0, MAX_PER_TAB))
    })
  },
  { urls: ['<all_urls>'], types: ['media', 'xmlhttprequest', 'other', 'object', 'sub_frame'] },
  ['responseHeaders'],
)

// A new page in the tab: forget what the old one played.
chrome.tabs.onUpdated.addListener((tabId, change) => {
  if (change.status === 'loading' && change.url) serial(() => setTabItems(tabId, []))
})
chrome.tabs.onRemoved.addListener((tabId) => {
  serial(() => chrome.storage.session.remove(`tab:${tabId}`))
})

// Right-click menu ---------------------------------------------------------------------------

chrome.runtime.onInstalled.addListener(() => {
  chrome.action.setBadgeBackgroundColor({ color: '#4f46e5' })
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: 'send',
      title: 'Send to LectureLab',
      contexts: ['page', 'link', 'video', 'audio', 'frame'],
    })
  })
})

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const url = info.srcUrl || info.linkUrl || info.frameUrl || info.pageUrl
  if (!url || url.startsWith('blob:')) {
    notify(
      'Nothing to send',
      'This video plays from a blob: URL. Open the popup and pick the stream it found.',
    )
    return
  }
  try {
    const subjects = await lecturelab.subjects()
    const subjectId = await defaultSubject(subjects)
    if (!subjectId) throw new Error('Create a subject in LectureLab first.')
    const {
      kind = '',
      video = true,
      sendCookies = true,
    } = await chrome.storage.local.get(['kind', 'video', 'sendCookies'])
    const lecture = await sendToLectureLab(
      { url, referer: info.pageUrl, pageUrl: tab?.url },
      { subjectId, kind, video, sendCookies },
    )
    const subject = subjects.find((s) => s.id === subjectId)
    notify('Sent to LectureLab', `Downloading into ${subject?.name ?? 'your subject'}.`, lecture.id)
  } catch (e) {
    notify('Could not send to LectureLab', e.message)
  }
})

function notify(title, message, lectureId) {
  const id = lectureId ? `lecture:${lectureId}` : ''
  chrome.notifications.create(id, { type: 'basic', iconUrl: 'icons/icon128.png', title, message })
}

chrome.notifications.onClicked.addListener(async (id) => {
  if (id.startsWith('lecture:')) chrome.tabs.create({ url: await lectureUrl(id.slice(8)) })
})
