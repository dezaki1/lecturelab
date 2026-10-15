import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, Radio, Send, TriangleAlert } from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'

import { Markdown } from '@/components/Markdown'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { useEvent } from '@/hooks/useEvents'
import { api } from '@/lib/api'
import { formatDuration } from '@/lib/dates'
import type { LiveAnswer, LiveSegment, LiveState } from '@/lib/types'
import { cn } from '@/lib/utils'

const QUICK = [
  'What did they just say?',
  'Explain the last part more simply',
  'Give me an example of what they just explained',
  'What was the formula they just used, and what do the symbols mean?',
]

/** Live mode: the transcript while recording (~15-20 s behind) and quick questions about it. */
export function LivePage() {
  const qc = useQueryClient()
  const { data: live } = useQuery({ queryKey: ['live'], queryFn: api.live })
  const { data: system } = useQuery({ queryKey: ['system'], queryFn: api.system })
  const set = (d: LiveState) => qc.setQueryData(['live'], d)
  useEvent('live.segment', (seg: LiveSegment & { lecture_id: number }) =>
    qc.setQueryData<LiveState>(['live'], (old) =>
      old && old.lecture_id === seg.lecture_id
        ? { ...old, segments: [...(old.segments ?? []), seg], error: '' }
        : old,
    ),
  )
  useEvent('live.answer', (a: LiveAnswer & { lecture_id: number }) =>
    qc.setQueryData<LiveState>(['live'], (old) =>
      old && !old.answers.some((x) => x.question === a.question && x.at_s === a.at_s)
        ? { ...old, answers: [...old.answers, a] }
        : old,
    ),
  )
  useEvent('live.error', () => qc.invalidateQueries({ queryKey: ['live'] }))
  useEvent('recorder.state', () => qc.invalidateQueries({ queryKey: ['live'] }))
  const toggle = useMutation({
    mutationFn: api.toggleLive,
    onSuccess: set,
    onError: (e: Error) => toast.error(e.message),
  })

  if (!live) return null
  if (!live.recording)
    return (
      <div className="mx-auto max-w-3xl px-4 py-10 sm:px-8">
        <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
          <Radio className="size-6" /> Live lecture
        </h1>
        <p className="mt-3 text-sm text-muted-foreground">
          Nothing is being recorded right now. While you record, this page shows the lecture as text about
          15–20 seconds behind, and you can ask quick questions like “what did they just say?”.
        </p>
        {!system?.remote && <DefaultSwitch enabled={live.enabled_default} />}
      </div>
    )

  return (
    <div className="mx-auto flex h-full max-w-5xl flex-col px-4 py-6 sm:px-8">
      <div className="flex flex-wrap items-center gap-3">
        <span className="relative flex size-2.5">
          <span className="absolute inline-flex size-full animate-ping rounded-full bg-red-500 opacity-60" />
          <span className="relative inline-flex size-2.5 rounded-full bg-red-500" />
        </span>
        <h1 className="min-w-0 truncate text-lg font-semibold">
          {live.subject_name}
          <span className="font-normal text-muted-foreground"> · {live.title}</span>
        </h1>
        <div className="ml-auto flex items-center gap-2 text-sm">
          <Switch
            id="live-on"
            checked={!!live.live}
            disabled={toggle.isPending}
            onCheckedChange={(on) => toggle.mutate(on)}
          />
          <Label htmlFor="live-on">Live transcript</Label>
        </div>
      </div>
      {live.error && (
        <p className="mt-2 flex items-center gap-1.5 text-sm text-amber-600 dark:text-amber-400">
          <TriangleAlert className="size-4" /> {live.error}
        </p>
      )}
      <div className="mt-4 grid min-h-0 flex-1 gap-4 lg:grid-cols-[3fr_2fr]">
        <Transcript segments={live.segments ?? []} on={!!live.live} lectureId={live.lecture_id!} />
        <Questions answers={live.answers} canAsk={(live.segments ?? []).length > 0} />
      </div>
    </div>
  )
}

function DefaultSwitch({ enabled }: { enabled: boolean }) {
  const qc = useQueryClient()
  const save = useMutation({
    mutationFn: api.putLiveSettings,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['live'] }),
    onError: (e: Error) => toast.error(e.message),
  })
  return (
    <div className="mt-6 flex items-center gap-2 rounded-lg border bg-card p-4 text-sm">
      <Switch id="live-default" checked={enabled} onCheckedChange={(v) => save.mutate(v)} />
      <Label htmlFor="live-default">Turn on the live transcript for every new recording</Label>
      <span className="ml-auto text-xs text-muted-foreground">Uses the GPU while recording</span>
    </div>
  )
}

function Transcript({
  segments,
  on,
  lectureId,
}: {
  segments: LiveSegment[]
  on: boolean
  lectureId: number
}) {
  const box = useRef<HTMLDivElement>(null)
  const stick = useRef(true)
  useEffect(() => {
    const el = box.current
    if (el && stick.current) el.scrollTop = el.scrollHeight
  }, [segments.length])
  return (
    <section className="flex min-h-[18rem] flex-col rounded-lg border bg-card">
      <div
        ref={box}
        onScroll={(e) => {
          const el = e.currentTarget
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40
        }}
        className="min-h-0 flex-1 space-y-2 overflow-y-auto p-4 text-sm leading-relaxed lg:max-h-[calc(100dvh-12rem)]"
      >
        {segments.map((s) => (
          <p key={s.start}>
            <Link
              to={`/lectures/${lectureId}?t=${Math.floor(s.start)}`}
              className="mr-2 font-mono text-xs text-muted-foreground hover:text-foreground"
            >
              {formatDuration(s.start)}
            </Link>
            {s.text}
          </p>
        ))}
        {!segments.length && (
          <p className="text-muted-foreground">
            {on
              ? 'Listening… the first lines appear about 15–20 seconds after speech starts.'
              : 'The live transcript is off. Switch it on above (it starts from the last half minute).'}
          </p>
        )}
      </div>
    </section>
  )
}

function Questions({ answers, canAsk }: { answers: LiveAnswer[]; canAsk: boolean }) {
  const [q, setQ] = useState('')
  const ask = useMutation({
    mutationFn: api.askLive,
    onSuccess: () => setQ(''),
    onError: (e: Error) => toast.error(e.message),
  })
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (q.trim()) ask.mutate(q.trim())
  }
  return (
    <section className="flex min-h-0 flex-col gap-3">
      <div className="flex flex-wrap gap-1.5">
        {QUICK.map((x) => (
          <Button
            key={x}
            size="xs"
            variant="outline"
            disabled={!canAsk || ask.isPending}
            onClick={() => ask.mutate(x)}
          >
            {x}
          </Button>
        ))}
      </div>
      <form onSubmit={submit} className="flex gap-2">
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Ask about what was just said…"
          aria-label="Question"
          disabled={!canAsk}
        />
        <Button type="submit" size="icon" disabled={!q.trim() || ask.isPending} aria-label="Ask">
          {ask.isPending ? <Loader2 className="animate-spin" /> : <Send />}
        </Button>
      </form>
      <ol className="min-h-0 flex-1 space-y-3 overflow-y-auto">
        {ask.isPending && (
          <li className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> Thinking…
          </li>
        )}
        {[...answers].reverse().map((a, i) => (
          <li key={`${a.at_s}-${i}`} className={cn('rounded-lg border bg-card p-3 text-sm')}>
            <div className="mb-1 flex items-baseline gap-2 text-xs text-muted-foreground">
              <span className="font-medium text-foreground">{a.question}</span>
              <span className="ml-auto shrink-0">at {formatDuration(a.at_s)}</span>
            </div>
            <Markdown>{a.answer}</Markdown>
            {a.refs.length > 0 && (
              <div className="mt-1 text-xs text-muted-foreground">
                from {a.refs.map((r) => r.label).join(', ')}
              </div>
            )}
          </li>
        ))}
      </ol>
    </section>
  )
}
