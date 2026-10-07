import { useEffect, useLayoutEffect, useRef } from 'react'

type Handler = (data: any) => void

// One shared EventSource for the whole app; components subscribe by event type.
let source: EventSource | null = null
const handlers = new Map<string, Set<Handler>>()

function ensureSource() {
  if (source) return
  source = new EventSource('/api/events')
  source.onerror = () => {
    // EventSource reconnects automatically; nothing to do.
  }
  for (const type of handlers.keys()) attach(type)
}

const attached = new Set<string>()
function attach(type: string) {
  if (!source || attached.has(type)) return
  attached.add(type)
  source.addEventListener(type, (e) => {
    const data = JSON.parse((e as MessageEvent).data)
    handlers.get(type)?.forEach((h) => h(data))
  })
}

export function useEvent(type: string, handler: Handler) {
  const ref = useRef(handler)
  useLayoutEffect(() => {
    ref.current = handler
  })
  useEffect(() => {
    const h: Handler = (d) => ref.current(d)
    if (!handlers.has(type)) handlers.set(type, new Set())
    handlers.get(type)!.add(h)
    ensureSource()
    attach(type)
    return () => {
      handlers.get(type)?.delete(h)
    }
  }, [type])
}
