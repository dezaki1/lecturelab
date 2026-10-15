import {
  defaultSubject,
  lectureUrl,
  lecturelab,
  previewOf,
  saveToDisk,
  sendToLectureLab,
  serverUrl,
} from './lib.js'

const $ = (id) => document.getElementById(id)
const mode = () => document.querySelector('input[name="mode"]:checked').value
const ACTION = { study: 'Send', save: 'Download' }
const relabel = new Set() // buttons of rows not sent yet follow the mode

function showError(msg) {
  $('error').textContent = msg
  $('error').hidden = !msg
}

function formatSize(bytes) {
  if (!bytes) return ''
  return bytes > 1e9 ? `${(bytes / 1e9).toFixed(1)} GB` : `${Math.max(0.1, bytes / 1e6).toFixed(1)} MB`
}

function shortName(url) {
  try {
    const u = new URL(url)
    const file = decodeURIComponent(u.pathname.split('/').filter(Boolean).pop() || '')
    return `${u.hostname} · ${file || '/'}`
  } catch {
    return url
  }
}

function options() {
  return {
    subjectId: Number($('subject').value),
    title: $('title').value.trim(),
    kind: $('kind').value,
    video: $('video').checked,
    sendCookies: $('cookies').checked,
  }
}

function formatDuration(s) {
  s = Math.round(s)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = String(s % 60).padStart(2, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`
}

// Direct files the popup can play itself; streams (HLS/DASH) and pages show the frame instead.
const playable = (item) => item.kind === 'Video' || item.kind === 'Audio'

/** Thumbnail, length, quality and size, from LectureLab (it reads the stream like the download). */
async function loadPreview(item, thumb, detail, fallback) {
  let p
  try {
    p = await previewOf(item, $('cookies').checked)
  } catch (e) {
    thumb.textContent = 'No preview'
    if (!item.kind) {
      detail.textContent = e.message.startsWith('No video found')
        ? 'No video found here. Play it and pick one below.'
        : e.message
      thumb.closest('li').classList.add('none')
    }
    return
  }
  const parts = [
    p.duration ? formatDuration(p.duration) : null,
    p.height ? `${p.height}p` : p.has_video === false ? 'audio' : null,
    // A sniffed stream's own size is its playlist's; only direct files' sizes count.
    formatSize(p.filesize || (playable(item) ? item.size : 0)) || null,
  ].filter(Boolean)
  detail.textContent = parts.join(' · ') || fallback
  if (!item.kind && p.title) thumb.closest('li').querySelector('.title').textContent = p.title
  thumb.textContent = ''
  if (p.thumbnail) {
    const img = document.createElement('img')
    img.src = p.thumbnail
    img.alt = ''
    img.referrerPolicy = 'no-referrer'
    img.onerror = () => img.replaceWith(playable(item) ? '▶' : 'No preview')
    thumb.append(img)
    thumb.dataset.image = p.thumbnail
  } else {
    thumb.textContent = p.has_video === false ? '♪' : playable(item) ? '▶' : 'No preview'
  }
  if (p.duration) {
    const badge = document.createElement('span')
    badge.className = 'duration'
    badge.textContent = formatDuration(p.duration)
    thumb.append(badge)
  }
}

/** Click on the thumbnail: play a direct file inline, else show the frame bigger. */
function togglePreview(item, li, thumb) {
  const open = li.querySelector('.preview')
  if (open) return open.remove()
  const box = document.createElement('div')
  box.className = 'preview'
  if (playable(item)) {
    const media = document.createElement(item.kind === 'Audio' ? 'audio' : 'video')
    media.src = item.url
    media.controls = true
    media.autoplay = true
    media.onerror = () => {
      box.textContent = 'This video only plays on its page. The frame above is what LectureLab sees.'
    }
    box.append(media)
  } else if (thumb.dataset.image) {
    const img = document.createElement('img')
    img.src = thumb.dataset.image
    img.alt = ''
    box.append(img)
  } else {
    return
  }
  li.append(box)
}

function itemRow(item, tags, name, detail) {
  const li = document.createElement('li')
  const thumb = document.createElement('div')
  thumb.className = 'thumb'
  thumb.textContent = '…'
  thumb.title = 'Preview'
  thumb.addEventListener('click', () => togglePreview(item, li, thumb))
  const info = document.createElement('div')
  info.className = 'info'
  const line = document.createElement('div')
  line.className = 'name'
  for (const t of tags) {
    const tag = document.createElement('span')
    tag.className = 'tag'
    tag.textContent = t
    line.append(tag)
  }
  const title = document.createElement('span')
  title.className = 'title'
  title.textContent = name
  line.append(title)
  line.title = item.url
  info.append(line)
  const d = document.createElement('div')
  d.className = 'muted small name'
  d.textContent = 'Loading preview…'
  info.append(d)
  loadPreview(item, thumb, d, detail)
  const btn = document.createElement('button')
  const label = () => (btn.textContent = ACTION[mode()])
  label()
  relabel.add(label)
  let after = null // what a click does once sent
  btn.addEventListener('click', async () => {
    if (after) return after()
    const m = mode()
    const opts = options()
    if (m === 'study' && !opts.subjectId) return showError('Create a subject in LectureLab first.')
    btn.disabled = true
    btn.textContent = m === 'study' ? 'Sending…' : 'Starting…'
    showError('')
    try {
      if (m === 'study') {
        const lecture = await sendToLectureLab(item, opts)
        btn.textContent = 'Open ✓'
        after = async () => chrome.tabs.create({ url: await lectureUrl(lecture.id) })
      } else {
        await saveToDisk(item, opts)
        btn.textContent = 'Saving ✓'
        btn.title = 'LectureLab tells you when the file is in your Downloads folder. Click to open it.'
        after = () => lecturelab.openFolder().catch((e) => showError(e.message))
      }
      relabel.delete(label)
      btn.classList.add('done')
    } catch (e) {
      showError(e.message)
      label()
    } finally {
      btn.disabled = false
    }
  })
  li.append(thumb, info, btn)
  return li
}

async function main() {
  // popup.html?tab=<id> opens the popup for that tab in a normal tab (debugging).
  const tabParam = Number(new URLSearchParams(location.search).get('tab'))
  const [tab] = tabParam
    ? [await chrome.tabs.get(tabParam)]
    : await chrome.tabs.query({ active: true, currentWindow: true })
  const stored = await chrome.storage.local.get(['kind', 'video', 'sendCookies', 'mode'])
  if (stored.mode === 'save') document.querySelector('input[value="save"]').checked = true
  const applyMode = () => {
    const save = mode() === 'save'
    $('study-fields').hidden = save
    $('folder').hidden = !save
    $('video-label').textContent = save
      ? 'Video (untick to save only the audio as .m4a)'
      : 'Download the video (to find slides)'
    for (const f of relabel) f()
  }
  for (const r of document.querySelectorAll('input[name="mode"]'))
    r.addEventListener('change', () => {
      chrome.storage.local.set({ mode: mode() })
      applyMode()
    })
  applyMode()
  $('folder').addEventListener('click', (e) => {
    e.preventDefault()
    lecturelab.openFolder().catch((err) => showError(err.message))
  })
  $('kind').value = stored.kind ?? ''
  $('video').checked = stored.video ?? true
  $('cookies').checked = stored.sendCookies ?? true
  for (const [id, key, prop] of [
    ['kind', 'kind', 'value'],
    ['video', 'video', 'checked'],
    ['cookies', 'sendCookies', 'checked'],
  ])
    $(id).addEventListener('change', () => chrome.storage.local.set({ [key]: $(id)[prop] }))
  $('subject').addEventListener('change', () =>
    chrome.storage.local.set({ subjectId: Number($('subject').value) }),
  )
  $('open').addEventListener('click', async (e) => {
    e.preventDefault()
    chrome.tabs.create({ url: await serverUrl() })
  })
  $('options').addEventListener('click', (e) => {
    e.preventDefault()
    chrome.runtime.openOptionsPage()
  })

  let subjects
  try {
    subjects = await lecturelab.subjects()
  } catch (e) {
    $('status').textContent = 'Not running'
    $('status').className = 'status bad'
    showError(e.message)
    return
  }
  $('status').textContent = 'Connected'
  $('status').className = 'status ok'
  for (const s of subjects) $('subject').append(new Option(s.name, String(s.id)))
  if (subjects.length) $('subject').value = String(await defaultSubject(subjects))
  $('form').hidden = false

  if (tab?.url && /^https?:/i.test(tab.url)) {
    $('page').append(
      itemRow(
        { url: tab.url, pageUrl: tab.url },
        ['Page'],
        tab.title || shortName(tab.url),
        'LectureLab finds the video on the page',
      ),
    )
  } else {
    $('page').closest('section').querySelector('h2').hidden = true
  }

  const key = `tab:${tab?.id}`
  const media = (await chrome.storage.session.get(key))[key] || []
  $('count').textContent = media.length ? `(${media.length})` : ''
  $('empty').hidden = media.length > 0
  for (const m of media) {
    const tags = [m.kind]
    const size = formatSize(m.size)
    // A stream's own name is its URL's ("index.m3u8"); save it under the tab's title.
    const item = { ...m, pageUrl: tab?.url, fileTitle: tab?.title }
    $('media').append(itemRow(item, tags, shortName(m.url), size))
  }
}

main()
