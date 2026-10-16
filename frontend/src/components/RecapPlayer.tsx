import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Download, FileText, Loader2, RefreshCw, Trash2, XCircle } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import { formatDuration } from '@/lib/dates'
import type { Recap } from '@/lib/types'
import { cn } from '@/lib/utils'

const STATUS: Record<string, string> = {
  queued: 'Waiting in the job queue…',
  writing: 'Writing the script…',
  speaking: 'Reading it aloud…',
}
const SPEEDS = [1, 1.25, 1.5, 1.75]

/** One recap: chaptered audio player (speed, lock-screen controls), script, download. */
export function RecapPlayer({ recap: r, onRemake }: { recap: Recap; onRemake?: () => void }) {
  const qc = useQueryClient()
  const audio = useRef<HTMLAudioElement>(null)
  const [time, setTime] = useState(0)
  const [speed, setSpeed] = useState(() => Number(localStorage.getItem('recap-speed') || 1))
  const [showScript, setShowScript] = useState(false)
  const remove = useMutation({
    mutationFn: () => api.deleteRecap(r.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['recaps'] }),
    onError: (e: Error) => toast.error(e.message),
  })
  const current = r.chapters.reduce((acc, c, i) => (time >= c.start_s ? i : acc), 0)

  useEffect(() => {
    if (audio.current) audio.current.playbackRate = speed
    try {
      localStorage.setItem('recap-speed', String(speed))
    } catch {
      /* storage unavailable */
    }
  }, [speed, r.status])

  const seek = (s: number) => {
    const a = audio.current
    if (!a) return
    a.currentTime = s
    a.play().catch(() => {})
  }

  // Lock screen / headphone controls on phones: title + previous/next chapter.
  const onPlay = () => {
    if (!('mediaSession' in navigator)) return
    navigator.mediaSession.metadata = new MediaMetadata({
      title: r.title,
      artist: 'LectureLab',
      artwork: [{ src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png' }],
    })
    const chapterAt = () =>
      r.chapters.reduce((acc, c, i) => ((audio.current?.currentTime ?? 0) >= c.start_s ? i : acc), 0)
    navigator.mediaSession.setActionHandler('nexttrack', () => {
      const next = r.chapters[chapterAt() + 1]
      if (next) seek(next.start_s)
    })
    navigator.mediaSession.setActionHandler('previoustrack', () => {
      const i = chapterAt()
      const back = (audio.current?.currentTime ?? 0) - r.chapters[i].start_s > 3 ? i : Math.max(0, i - 1)
      seek(r.chapters[back]?.start_s ?? 0)
    })
  }

  if (r.status !== 'ready')
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        {r.status === 'failed' ? (
          <>
            <XCircle className="size-4 text-destructive" />{' '}
            <span className="text-destructive">{r.error}</span>
            {onRemake && (
              <Button size="xs" variant="outline" onClick={onRemake}>
                <RefreshCw /> Try again
              </Button>
            )}
          </>
        ) : (
          <>
            <Loader2 className="size-4 animate-spin" /> {STATUS[r.status]}
          </>
        )}
      </div>
    )

  return (
    <div className="space-y-3">
      {r.error && (
        <p className="flex flex-wrap items-center gap-2 text-xs text-amber-600 dark:text-amber-400">
          {r.error}
          {onRemake && (
            <Button size="xs" variant="outline" onClick={onRemake}>
              <RefreshCw /> Remake
            </Button>
          )}
        </p>
      )}
      <audio
        ref={audio}
        controls
        preload="metadata"
        src={`${api.recapAudioUrl(r.id)}?v=${encodeURIComponent(r.updated_at)}`} // ids can be reused
        className="w-full"
        onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
        onPlay={onPlay}
        onLoadedMetadata={(e) => (e.currentTarget.playbackRate = speed)}
      />
      <div className="flex flex-wrap items-center gap-1">
        {SPEEDS.map((x) => (
          <Button
            key={x}
            size="xs"
            variant={speed === x ? 'secondary' : 'ghost'}
            aria-pressed={speed === x}
            onClick={() => setSpeed(x)}
          >
            {x}×
          </Button>
        ))}
        <span className="ml-2 text-xs text-muted-foreground">{formatDuration(r.duration_s)}</span>
        <div className="ml-auto flex gap-1">
          <Button size="xs" variant="ghost" onClick={() => setShowScript(!showScript)}>
            <FileText /> {showScript ? 'Hide script' : 'Script'}
          </Button>
          <Button size="xs" variant="ghost" asChild>
            <a href={api.recapAudioUrl(r.id, true)} download>
              <Download /> .m4a
            </a>
          </Button>
          {onRemake && (
            <Button size="xs" variant="ghost" onClick={onRemake} title="Write and record it again">
              <RefreshCw /> Remake
            </Button>
          )}
          <Button size="icon-xs" variant="ghost" aria-label="Delete recap" onClick={() => remove.mutate()}>
            <Trash2 />
          </Button>
        </div>
      </div>
      <ol className="space-y-0.5 text-sm">
        {r.chapters.map((c, i) => (
          <li key={i} className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => seek(c.start_s)}
              className={cn(
                'flex min-w-0 flex-1 items-center gap-2 rounded px-1.5 py-0.5 text-left hover:bg-accent',
                i === current && time > 0 && 'bg-accent font-medium',
              )}
            >
              <span className="w-12 shrink-0 font-mono text-xs text-muted-foreground tabular-nums">
                {formatDuration(c.start_s)}
              </span>
              <span className="truncate">{c.title}</span>
            </button>
            {c.refs[0] && (
              <Link
                to={`/lectures/${c.refs[0].lecture_id}?t=${Math.floor(c.refs[0].start_s)}`}
                className="shrink-0 rounded bg-muted px-1 font-mono text-[11px] text-muted-foreground hover:text-foreground"
                title={`In the lecture: ${c.refs[0].title}`}
              >
                {c.refs[0].at}
              </Link>
            )}
          </li>
        ))}
      </ol>
      {showScript && (
        <div className="space-y-3 rounded-md border bg-muted/30 p-3 text-sm">
          {r.script.map((x, i) => (
            <div key={i}>
              <h4 className="font-medium">{x.title}</h4>
              <p className="mt-0.5 whitespace-pre-wrap text-muted-foreground">{x.text}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
