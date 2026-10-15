import { DEFAULT_SERVER, lecturelab, serverUrl } from './lib.js'

const input = document.getElementById('server')
const result = document.getElementById('result')

input.value = await serverUrl()

document.getElementById('save').addEventListener('click', async () => {
  const value = input.value.trim().replace(/\/+$/, '') || DEFAULT_SERVER
  await chrome.storage.local.set({ server: value })
  input.value = value
  try {
    await lecturelab.subjects()
    result.textContent = 'Saved. LectureLab answers ✓'
  } catch (e) {
    result.textContent = `Saved, but: ${e.message}`
  }
})
