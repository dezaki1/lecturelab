import { defaultSubject, lectureUrl, lecturelab, sendToLectureLab, serverUrl } from './lib.js'

const $ = (id) => document.getElementById(id)

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

function itemRow(item, tags, name, detail) {
  const li = document.createElement('li')
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
  line.append(name)
  line.title = item.url
  info.append(line)
  if (detail) {
    const d = document.createElement('div')
    d.className = 'muted small name'
    d.textContent = detail
    info.append(d)
  }
  const btn = document.createElement('button')
  btn.textContent = 'Send'
  let lecture = null
  btn.addEventListener('click', async () => {
    if (lecture) {
      chrome.tabs.create({ url: await lectureUrl(lecture.id) })
      return
    }
    btn.disabled = true
    btn.textContent = 'Sending…'
    showError('')
    try {
      lecture = await sendToLectureLab(item, options())
      btn.textContent = 'Open ✓'
      btn.classList.add('done')
      btn.disabled = false
    } catch (e) {
      showError(e.message)
      btn.disabled = false
      btn.textContent = 'Send'
    }
  })
  li.append(info, btn)
  return li
}

async function main() {
  // popup.html?tab=<id> opens the popup for that tab in a normal tab (debugging).
  const tabParam = Number(new URLSearchParams(location.search).get('tab'))
  const [tab] = tabParam
    ? [await chrome.tabs.get(tabParam)]
    : await chrome.tabs.query({ active: true, currentWindow: true })
  const stored = await chrome.storage.local.get(['kind', 'video', 'sendCookies'])
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
  if (!subjects.length) {
    showError('Create a subject in LectureLab first.')
    return
  }
  for (const s of subjects) $('subject').append(new Option(s.name, String(s.id)))
  $('subject').value = String(await defaultSubject(subjects))
  $('form').hidden = false

  if (tab?.url && /^https?:/i.test(tab.url)) {
    $('page').append(
      itemRow(
        { url: tab.url, pageUrl: tab.url },
        ['Page'],
        tab.title || shortName(tab.url),
        'LectureLab finds the video',
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
    $('media').append(itemRow({ ...m, pageUrl: tab?.url }, tags, shortName(m.url), size))
  }
}

main()
