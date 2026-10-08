import { ChevronDown, ChevronUp, CircleHelp, Crosshair, Search, Star } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { formatDuration } from '@/lib/dates'
import type { Bookmark, Segment } from '@/lib/types'
import { cn } from '@/lib/utils'

const LOW_CONFIDENCE = 0.45

/** Index of the segment playing at time t (last segment starting at or before t). */
export function activeIndex(segments: Segment[], t: number): number {
  let lo = 0
  let hi = segments.length - 1
  let ans = -1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    if (segments[mid].start <= t) {
      ans = mid
      lo = mid + 1
    } else hi = mid - 1
  }
  return ans
}

function highlight(text: string, query: string): ReactNode {
  if (!query) return text
  const parts = text.split(new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi'))
  return parts.map((p, i) =>
    i % 2 === 1 ? (
      <mark key={i} className="rounded bg-amber-300/70 px-0.5 text-foreground dark:bg-amber-500/40">
        {p}
      </mark>
    ) : (
      p
    ),
  )
}

export function TranscriptView({
  segments,
  bookmarks,
  currentTime,
  onSeek,
}: {
  segments: Segment[]
  bookmarks: Bookmark[]
  currentTime: number
  onSeek: (t: number) => void
}) {
  const [query, setQuery] = useState('')
  const [matchPos, setMatchPos] = useState(0)
  const [follow, setFollow] = useState(true)
  const rowRefs = useRef<(HTMLLIElement | null)[]>([])
  const searchRef = useRef<HTMLInputElement>(null)
  const programmaticScroll = useRef(0)

  const active = activeIndex(segments, currentTime)
  const q = query.trim()
  const matches = useMemo(
    () => (q ? segments.flatMap((s, i) => (s.text.toLowerCase().includes(q.toLowerCase()) ? [i] : [])) : []),
    [segments, q],
  )

  // Bookmarks shown next to the segment they fall into.
  const marks = useMemo(() => {
    const m = new Map<number, Bookmark[]>()
    for (const b of bookmarks) {
      const i = Math.max(0, activeIndex(segments, b.t_s))
      m.set(i, [...(m.get(i) ?? []), b])
    }
    return m
  }, [bookmarks, segments])

  const scrollTo = (i: number) => {
    programmaticScroll.current = Date.now()
    rowRefs.current[i]?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }

  useEffect(() => {
    if (follow && active >= 0 && !q) scrollTo(active)
  }, [active, follow, q])

  useEffect(() => {
    if (matches.length) scrollTo(matches[Math.min(matchPos, matches.length - 1)])
  }, [matches, matchPos])

  // Manual scrolling pauses auto-follow.
  useEffect(() => {
    const onWheel = () => {
      if (Date.now() - programmaticScroll.current > 800) setFollow(false)
    }
    window.addEventListener('wheel', onWheel, { passive: true })
    window.addEventListener('touchmove', onWheel, { passive: true })
    return () => {
      window.removeEventListener('wheel', onWheel)
      window.removeEventListener('touchmove', onWheel)
    }
  }, [])

  // "/" focuses the search box.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName
      if (e.key === '/' && tag !== 'INPUT' && tag !== 'TEXTAREA') {
        e.preventDefault()
        searchRef.current?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const step = (d: number) => matches.length && setMatchPos((p) => (p + d + matches.length) % matches.length)

  return (
    <div>
      <div className="sticky top-[var(--player-h,0px)] z-10 -mx-1 flex items-center gap-2 bg-background/95 px-1 py-2 backdrop-blur">
        <div className="relative flex-1">
          <Search className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            ref={searchRef}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value)
              setMatchPos(0)
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') step(e.shiftKey ? -1 : 1)
              if (e.key === 'Escape') {
                setQuery('')
                e.currentTarget.blur()
              }
            }}
            placeholder="Search transcript  ( / )"
            className="pl-8"
          />
        </div>
        {q && (
          <>
            <span className="w-16 text-center text-xs text-muted-foreground tabular-nums">
              {matches.length ? `${Math.min(matchPos, matches.length - 1) + 1} / ${matches.length}` : '0 / 0'}
            </span>
            <Button size="icon-sm" variant="ghost" aria-label="Previous match" onClick={() => step(-1)}>
              <ChevronUp />
            </Button>
            <Button size="icon-sm" variant="ghost" aria-label="Next match" onClick={() => step(1)}>
              <ChevronDown />
            </Button>
          </>
        )}
        {!follow && !q && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              setFollow(true)
              if (active >= 0) scrollTo(active)
            }}
          >
            <Crosshair /> Follow playback
          </Button>
        )}
      </div>

      <ol className="mt-1 space-y-0.5">
        {segments.map((s, i) => {
          const isActive = i === active
          const isMatch = q && matches[Math.min(matchPos, matches.length - 1)] === i
          const low = s.confidence != null && s.confidence < LOW_CONFIDENCE
          return (
            <li
              key={s.id}
              ref={(el) => {
                rowRefs.current[i] = el
              }}
              onClick={() => onSeek(s.start)}
              className={cn(
                'group flex cursor-pointer gap-3 rounded-md px-2 py-1.5 transition-colors',
                isActive ? 'bg-primary/10' : 'hover:bg-muted/60',
                isMatch && 'ring-1 ring-amber-500/60',
              )}
            >
              <span
                className={cn(
                  'w-12 shrink-0 pt-0.5 text-right font-mono text-xs tabular-nums',
                  isActive ? 'text-primary' : 'text-muted-foreground',
                )}
              >
                {formatDuration(s.start)}
              </span>
              <p
                className={cn(
                  'flex-1 leading-relaxed',
                  low &&
                    'decoration-amber-500/70 decoration-dotted underline-offset-4 [text-decoration-line:underline]',
                )}
                title={
                  low ? `Low transcription confidence (${Math.round((s.confidence ?? 0) * 100)}%)` : undefined
                }
              >
                {highlight(s.text, q)}
              </p>
              {marks
                .get(i)
                ?.map((b) =>
                  b.kind === 'important' ? (
                    <Star key={b.id} className="mt-1 size-3.5 shrink-0 fill-amber-400 text-amber-500" />
                  ) : (
                    <CircleHelp key={b.id} className="mt-1 size-3.5 shrink-0 text-sky-500" />
                  ),
                )}
            </li>
          )
        })}
      </ol>
    </div>
  )
}
