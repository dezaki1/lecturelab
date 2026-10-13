import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

// Installable app (tablet/phone via LAN mode). Service workers need a secure context:
// localhost, or a LAN address the browser was told to trust (see README, Tablet & phone access).
if (
  import.meta.env.PROD &&
  'serviceWorker' in navigator &&
  window.isSecureContext &&
  !window.location.pathname.startsWith('/print/')
) {
  navigator.serviceWorker.register('/sw.js').catch(() => {})
}
