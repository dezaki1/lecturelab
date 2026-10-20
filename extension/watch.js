// Watched Moodle courses. Once a minute the background asks LectureLab which courses are due
// (it keeps the schedule: hourly, more often after a lecture, "Check now"). For those, this
// reads the course page with the browser's Moodle login, opens Opencast recordings it doesn't
// know yet and reports them: episode id, title, length and the signed video link (it works
// without cookies, so LectureLab downloads it by itself). Nothing here decides what to import.

import { lecturelab } from './lib.js'

const EPISODE = 'window.episode = '
const HEIGHT = 720 // enough for slides; the 1080p file is twice the size

const decode = (s) =>
  s
    .replace(/&amp;/g, '&')
    .replace(/&quot;/g, '"')
    .replace(/&#0?39;/g, "'")
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&nbsp;/g, ' ')
const text = (html) =>
  decode(html.replace(/<span[^>]*accesshide[^>]*>[\s\S]*?<\/span>/g, ' ').replace(/<[^>]*>/g, ' '))
    .replace(/\s+/g, ' ')
    .trim()

class LoggedOut extends Error {}

async function get(url) {
  const res = await fetch(url, { credentials: 'include', redirect: 'follow' })
  const html = await res.text()
  // Logged out, Moodle redirects to its login page (or the university's single sign-on).
  if (!res.ok || !res.url.includes(new URL(url).pathname) || html.includes('id="page-login-index"'))
    throw new LoggedOut()
  return html
}

function episodeIn(html) {
  const i = html.indexOf(EPISODE)
  if (i < 0) return null
  try {
    return JSON.parse(
      html
        .slice(i + EPISODE.length, html.indexOf('\n', i))
        .trim()
        .replace(/;$/, ''),
    )
  } catch {
    return null
  }
}

const mp4s = (stream) => {
  const mp4 = stream?.sources?.mp4
  return Array.isArray(mp4) ? [...mp4] : Object.values(mp4 || {})
}

/** Opencast records two videos: the slides/screen ("presentation") and the camera
 * ("presenter"). The sound is on one of them ("mainAudio"), which one depends on the room. The
 * video LectureLab wants is the slides one (~720p); if the sound is on the camera, its smallest
 * file comes along as `audio_url` and LectureLab puts the two together. */
function videoUrls(episode) {
  const streams = episode.streams || []
  const sound = streams.find((s) => s.role === 'mainAudio') || streams[0]
  const slides = streams.find((s) => s.content === 'presentation') || sound
  const video = mp4s(slides).sort(
    (a, b) => Math.abs((a.res?.h || 0) - HEIGHT) - Math.abs((b.res?.h || 0) - HEIGHT),
  )[0]
  const audio = slides === sound ? null : mp4s(sound).sort((a, b) => (a.res?.h || 0) - (b.res?.h || 0))[0]
  return { url: video?.src || '', audio_url: audio?.src || '' }
}

function links(html, base, re) {
  const out = new Map()
  for (const m of html.matchAll(re)) {
    const url = new URL(decode(m[1]), base).href
    if (!out.has(m[2])) out.set(m[2], { id: m[2], url, name: text(m[3]) })
  }
  return [...out.values()]
}

// Opencast activities on the course page: one recording each, or a whole series.
const ACTIVITY = /<a[^>]+href="([^"]*\/mod\/opencast\/view\.php\?id=(\d+))"[^>]*>([\s\S]*?)<\/a>/g
// Recordings listed on a series activity's page.
const EPISODE_LINK =
  /<a[^>]+href="([^"]*\/mod\/opencast\/view\.php\?id=\d+&amp;e=([0-9a-f-]{36})[^"]*)"[^>]*>([\s\S]*?)<\/a>/g

async function scanCourse(course) {
  const base = `https://${course.host}`
  const known = new Set(course.known)
  const found = new Map()
  const add = (episode, title, pageUrl) => {
    const id = episode?.metadata?.id
    if (!id || found.has(id)) return
    found.set(id, {
      episode_id: id,
      title: title || episode.metadata.title || '',
      oc_title: episode.metadata.title || '',
      duration_s: episode.metadata.duration || 0,
      ...(known.has(id) ? { url: '', audio_url: '' } : videoUrls(episode)),
      page_url: pageUrl,
    })
  }
  const report = (body) =>
    lecturelab.watchReport({ host: course.host, moodle_id: course.moodle_id, ...body }).catch(() => {})
  try {
    const page = await get(`${base}/course/view.php?id=${course.moodle_id}`)
    for (const act of links(page, base, ACTIVITY)) {
      // Single-recording activities: remember which episode they hold, open them only once.
      const key = `oc:${course.host}:${act.id}`
      const cached = (await chrome.storage.local.get(key))[key]
      if (cached && cached !== 'series' && known.has(cached)) continue
      const html = await get(act.url)
      const episode = episodeIn(html)
      if (episode) {
        await chrome.storage.local.set({ [key]: episode.metadata.id })
        add(episode, act.name, act.url)
        continue
      }
      await chrome.storage.local.set({ [key]: 'series' })
      for (const ep of links(html, base, EPISODE_LINK)) {
        if (known.has(ep.id) || found.has(ep.id)) continue
        add(episodeIn(await get(ep.url)), ep.name, ep.url)
      }
    }
  } catch (e) {
    const error =
      e instanceof LoggedOut
        ? `Not logged in to ${course.host}: open Moodle in Chrome/Edge and log in, LectureLab then checks again.`
        : `Couldn't read ${course.host}: ${e.message}`
    return report({ error })
  }
  return report({ recordings: [...found.values()] })
}

let running = false

export async function watchTick() {
  if (running) return
  running = true
  try {
    let state
    try {
      state = await lecturelab.watchState()
    } catch {
      return // LectureLab isn't running
    }
    if (!state.scan) return
    for (const course of state.courses) if (course.due) await scanCourse(course)
  } finally {
    running = false
  }
}

/** The Moodle course a tab shows, if any: { host, moodleId }. */
export function moodleCourse(url) {
  try {
    const u = new URL(url)
    const id = Number(u.searchParams.get('id'))
    if (u.pathname.endsWith('/course/view.php') && id) return { host: u.host, moodleId: id }
  } catch {
    /* not a URL */
  }
  return null
}
