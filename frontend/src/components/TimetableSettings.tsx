import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CalendarDays, Link2, Loader2, RefreshCw, Trash2, Upload, XCircle } from 'lucide-react'
import { useRef, useState, type FormEvent } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { useEvent } from '@/hooks/useEvents'
import { api } from '@/lib/api'
import type { TimetableCourse } from '@/lib/types'

const when = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' }) : 'never'

/** Settings card: calendar sources, course -> subject mapping, reminders, auto-record. */
export function TimetableSettings() {
  const qc = useQueryClient()
  const { data: t } = useQuery({ queryKey: ['timetable'], queryFn: api.timetable })
  const { data: subjects = [] } = useQuery({ queryKey: ['subjects'], queryFn: api.subjects })
  const [url, setUrl] = useState('')
  const file = useRef<HTMLInputElement>(null)
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['timetable'] })
    qc.invalidateQueries({ queryKey: ['timetable-now'] })
  }
  useEvent('timetable', refresh)
  const onError = (e: Error) => toast.error(e.message)

  const addLink = useMutation({
    mutationFn: () => api.addTimetableLink(url.trim()),
    onSuccess: () => {
      setUrl('')
      refresh()
    },
    onError,
  })
  const upload = useMutation({ mutationFn: api.uploadTimetable, onSuccess: refresh, onError })
  const sync = useMutation({ mutationFn: api.syncTimetable, onSuccess: refresh, onError })
  const remove = useMutation({ mutationFn: api.deleteTimetable, onSuccess: refresh, onError })
  const course = useMutation({
    mutationFn: (v: { id: number; subject_id?: number; ignored?: boolean }) =>
      api.updateTimetableCourse(v.id, v),
    onSuccess: refresh,
    onError,
  })
  const settings = useMutation({
    mutationFn: api.putTimetableSettings,
    onSuccess: (s) => qc.setQueryData(['timetable'], { ...t!, settings: s }),
    onError,
  })
  if (!t) return null

  return (
    <section className="mt-6 space-y-4 rounded-lg border bg-card p-5 text-sm">
      <div>
        <h2 className="flex items-center gap-2 font-semibold">
          <CalendarDays className="size-4" /> Timetable
        </h2>
        <p className="mt-1 text-muted-foreground">
          Add your KUSSS calendar (or any .ics). LectureLab then knows which course is on: recordings started
          by hotkey or tray get the right subject and type, you get a reminder before each lecture, and it can
          start recording by itself when Zoom is open.
        </p>
      </div>

      <form
        className="flex gap-2"
        onSubmit={(e: FormEvent) => {
          e.preventDefault()
          if (url.trim()) addLink.mutate()
        }}
      >
        <Label htmlFor="tt-url" className="sr-only">
          Calendar link
        </Label>
        <Input
          id="tt-url"
          placeholder="Calendar link (https://… or webcal://…)"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          spellCheck={false}
        />
        <Button type="submit" variant="outline" disabled={!url.trim() || addLink.isPending}>
          {addLink.isPending ? <Loader2 className="animate-spin" /> : <Link2 />} Subscribe
        </Button>
        <Button
          type="button"
          variant="outline"
          onClick={() => file.current?.click()}
          disabled={upload.isPending}
        >
          {upload.isPending ? <Loader2 className="animate-spin" /> : <Upload />} .ics file
        </Button>
        <input
          ref={file}
          type="file"
          accept=".ics,text/calendar"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) upload.mutate(f)
            e.target.value = ''
          }}
        />
      </form>

      {t.sources.length > 0 && (
        <ul className="divide-y rounded-md border">
          {t.sources.map((s) => (
            <li key={s.id} className="flex items-center gap-3 px-3 py-2">
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium">{s.name}</div>
                <div className="truncate text-xs text-muted-foreground">
                  {s.is_file ? 'Uploaded file' : `Link · synced ${when(s.last_sync_at)}`}
                </div>
                {s.error && (
                  <div className="flex items-center gap-1 text-xs text-destructive">
                    <XCircle className="size-3" /> {s.error}
                  </div>
                )}
              </div>
              {!s.is_file && (
                <Button
                  size="icon"
                  variant="ghost"
                  title="Sync now"
                  disabled={sync.isPending}
                  onClick={() => sync.mutate(s.id)}
                >
                  <RefreshCw className={sync.isPending && sync.variables === s.id ? 'animate-spin' : ''} />
                </Button>
              )}
              <Button size="icon" variant="ghost" title="Remove" onClick={() => remove.mutate(s.id)}>
                <Trash2 />
              </Button>
            </li>
          ))}
        </ul>
      )}

      {t.courses.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-xs font-medium text-muted-foreground">Courses in your calendar</h3>
          <ul className="space-y-1.5">
            {t.courses.map((c) => (
              <CourseRow
                key={c.id}
                c={c}
                subjects={subjects}
                onChange={(body) => course.mutate({ id: c.id, ...body })}
              />
            ))}
          </ul>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-4 border-t pt-4">
        <div className="flex items-center gap-2">
          <Label htmlFor="tt-remind">Remind me</Label>
          <Select
            value={String(t.settings.remind_min)}
            onValueChange={(v) => settings.mutate({ remind_min: Number(v) })}
          >
            <SelectTrigger id="tt-remind" size="sm" className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="0">never</SelectItem>
              {[1, 2, 5, 10, 15].map((m) => (
                <SelectItem key={m} value={String(m)}>
                  {m} min before
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex items-center gap-2">
          <Switch
            id="tt-auto"
            checked={t.settings.auto_record}
            onCheckedChange={(auto_record) => settings.mutate({ auto_record })}
          />
          <Label htmlFor="tt-auto">Record automatically when Zoom is open during a lecture</Label>
        </div>
      </div>
    </section>
  )
}

function CourseRow({
  c,
  subjects,
  onChange,
}: {
  c: TimetableCourse
  subjects: { id: number; name: string }[]
  onChange: (body: { subject_id?: number; ignored?: boolean }) => void
}) {
  return (
    <li className="flex items-center gap-3">
      <div className={`min-w-0 flex-1 ${c.ignored ? 'text-muted-foreground line-through' : ''}`}>
        <div className="truncate" title={c.label}>
          {c.label}
        </div>
        <div className="text-xs text-muted-foreground">
          {c.events} session{c.events === 1 ? '' : 's'}
          {c.subject_id && c.mapped_by === 'auto' && ' · matched automatically'}
        </div>
      </div>
      <Select
        value={String(c.subject_id ?? 0)}
        onValueChange={(v) => onChange({ subject_id: Number(v) })}
        disabled={c.ignored}
      >
        <SelectTrigger size="sm" className="w-56">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="0">No subject</SelectItem>
          {subjects.map((s) => (
            <SelectItem key={s.id} value={String(s.id)}>
              {s.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <div className="flex items-center gap-1.5">
        <Switch
          id={`tt-ign-${c.id}`}
          checked={!c.ignored}
          onCheckedChange={(on) => onChange({ ignored: !on })}
          title="Use this course"
        />
      </div>
    </li>
  )
}
