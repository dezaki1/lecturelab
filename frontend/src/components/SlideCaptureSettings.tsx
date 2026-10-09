import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Camera, Loader2, Presentation } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { api } from '@/lib/api'
import type { SlideSettings, SlideTest } from '@/lib/types'

/** Settings card: slide capture default, fallback monitor, extra window titles, test capture. */
export function SlideCaptureSettings() {
  const qc = useQueryClient()
  const { data: s } = useQuery({ queryKey: ['slide-settings'], queryFn: api.slideSettings })
  const [test, setTest] = useState<SlideTest | null>(null)
  const save = useMutation({
    mutationFn: api.putSlideSettings,
    onSuccess: (d: SlideSettings) => {
      qc.setQueryData(['slide-settings'], d)
      qc.invalidateQueries({ queryKey: ['system'] })
      setTest(null)
    },
    onError: (e: Error) => toast.error(e.message),
  })
  const runTest = useMutation({
    mutationFn: api.testSlideCapture,
    onSuccess: setTest,
    onError: (e: Error) => toast.error(e.message),
  })
  if (!s) return null

  return (
    <section className="mt-6 space-y-4 rounded-lg border bg-card p-5">
      <div>
        <h2 className="flex items-center gap-2 font-semibold">
          <Presentation className="size-4" /> Slide capture
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          While recording, LectureLab looks at the Zoom meeting window every {s.interval_s} s and keeps a
          picture whenever the slide changes. Speaker videos are ignored. Slide text is read on this computer.
        </p>
      </div>

      <div className="flex items-center justify-between gap-4">
        <Label htmlFor="slides-default">Capture slides in new recordings</Label>
        <Switch
          id="slides-default"
          checked={s.enabled}
          onCheckedChange={(enabled) => save.mutate({ enabled })}
        />
      </div>

      <div className="flex items-center justify-between gap-4">
        <div>
          <Label>If the Zoom window isn&apos;t found</Label>
          <p className="text-xs text-muted-foreground">
            e.g. when slides are shown in a browser or another app on a second monitor
          </p>
        </div>
        <Select
          value={String(s.fallback_monitor ?? 0)}
          onValueChange={(v) => save.mutate({ fallback_monitor: Number(v) })}
        >
          <SelectTrigger size="sm" className="w-56">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="0">Don&apos;t capture</SelectItem>
            {s.monitors.map((m) => (
              <SelectItem key={m.index} value={String(m.index)}>
                Capture {m.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <TitlesInput
        key={s.window_titles.join('|')}
        value={s.window_titles}
        onSave={(window_titles) => save.mutate({ window_titles })}
      />

      <div className="space-y-3">
        <Button variant="outline" size="sm" onClick={() => runTest.mutate()} disabled={runTest.isPending}>
          {runTest.isPending ? <Loader2 className="animate-spin" /> : <Camera />} Test capture
        </Button>
        {test?.found && (
          <figure className="space-y-1">
            <img src={test.preview} alt="Capture preview" className="max-w-md rounded-md border" />
            <figcaption className="text-xs text-muted-foreground">
              {test.source}
              {test.window_title ? ` “${test.window_title}”` : ''} · {test.size[0]}×{test.size[1]} px
            </figcaption>
          </figure>
        )}
        {test && !test.found && (
          <div className="text-sm">
            <p className="text-amber-600 dark:text-amber-400">{test.message}</p>
            {test.windows.length > 0 && (
              <p className="mt-1 text-xs text-muted-foreground">
                Open windows: {test.windows.map((w) => `“${w}”`).join(', ')}. Add the meeting window&apos;s
                title above if Zoom uses a different one.
              </p>
            )}
          </div>
        )}
      </div>
    </section>
  )
}

function TitlesInput({ value, onSave }: { value: string[]; onSave: (v: string[]) => void }) {
  const [draft, setDraft] = useState(value.join(', '))
  const commit = () => {
    const titles = draft
      .split(',')
      .map((t) => t.trim())
      .filter(Boolean)
    if (titles.join('|') !== value.join('|')) onSave(titles)
  }
  return (
    <div className="space-y-1.5">
      <Label htmlFor="slide-titles">Meeting window titles</Label>
      <Input
        id="slide-titles"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
      />
      <p className="text-xs text-muted-foreground">
        Comma-separated parts of a window title. Zoom&apos;s meeting window is also recognised by its window
        class.
      </p>
    </div>
  )
}
