import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, Link2, Loader2, Search } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { api } from '@/lib/api'
import { formatDuration } from '@/lib/dates'
import type { LinkInfo } from '@/lib/types'

/** "Import from link": yt-dlp finds the video behind a page or media URL and downloads it. */
export function LinkImportDialog({ subjectId, onClose }: { subjectId: number; onClose: () => void }) {
  const qc = useQueryClient()
  const { data: subjects = [] } = useQuery({ queryKey: ['subjects'], queryFn: api.subjects })
  const [url, setUrl] = useState('')
  const [info, setInfo] = useState<LinkInfo | null>(null)
  const [title, setTitle] = useState('')
  const [kind, setKind] = useState('')
  const [video, setVideo] = useState(true)
  const [subject, setSubject] = useState(String(subjectId))

  const probe = useMutation({
    mutationFn: () => api.probeLink(url.trim()),
    onSuccess: (i) => {
      setInfo(i)
      setTitle(i.title)
      setVideo(i.has_video)
    },
    onError: () => setInfo(null),
  })
  const start = useMutation({
    mutationFn: () =>
      api.importLink({ url: url.trim(), subject_id: Number(subject), title: title.trim(), kind, video }),
    onSuccess: () => {
      toast.success('Download started', { description: 'Transcription follows automatically.' })
      qc.invalidateQueries({ queryKey: ['lectures'] })
      qc.invalidateQueries({ queryKey: ['subjects'] })
      onClose()
    },
    onError: (e) => toast.error('Could not start the download', { description: (e as Error).message }),
  })
  const save = useMutation({
    mutationFn: () => api.saveLink({ url: url.trim(), video, title: title.trim() }),
    onSuccess: (r) => {
      toast.success('Downloading', {
        description: `Saved to ${r.folder} when done (see Jobs).`,
        action: { label: 'Open folder', onClick: () => api.openDownloadsFolder().catch(() => {}) },
      })
      onClose()
    },
    onError: (e) => toast.error('Could not start the download', { description: (e as Error).message }),
  })

  const live = info?.is_live ?? false
  const validUrl = /^https?:\/\/\S+$/i.test(url.trim())

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Import from link</DialogTitle>
          <DialogDescription>
            Paste a lecture page or video link (Moodle, Panopto, Zoom cloud recording, YouTube…). For pages
            that need a login, use the LectureLab browser extension instead: it sends your login along.
          </DialogDescription>
        </DialogHeader>

        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            if (validUrl) probe.mutate()
          }}
        >
          <Input
            autoFocus
            placeholder="https://…"
            value={url}
            onChange={(e) => {
              setUrl(e.target.value)
              setInfo(null)
              probe.reset()
            }}
            aria-label="Link"
          />
          <Button type="submit" variant="outline" disabled={!validUrl || probe.isPending}>
            {probe.isPending ? <Loader2 className="animate-spin" /> : <Search />} Check
          </Button>
        </form>

        {probe.isError && <p className="text-sm text-destructive">{(probe.error as Error).message}</p>}

        {info && (
          <div className="flex gap-3 rounded-lg border p-3">
            {info.thumbnail && (
              <img src={info.thumbnail} alt="" className="h-16 w-28 shrink-0 rounded object-cover" />
            )}
            <div className="min-w-0 text-sm">
              <p className="truncate font-medium">{info.title || 'Untitled video'}</p>
              <p className="text-muted-foreground">
                {[
                  info.extractor,
                  info.duration ? formatDuration(info.duration) : null,
                  info.has_video ? (info.height ? `${info.height}p` : 'video') : 'audio only',
                  info.filesize ? `${(info.filesize / 1e6).toFixed(0)} MB` : null,
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </p>
              {live && (
                <p className="text-destructive">
                  This is a live stream. Record it with the recorder instead.
                </p>
              )}
            </div>
          </div>
        )}

        <div className="grid gap-3 sm:grid-cols-[1fr_8rem]">
          <div className="grid gap-1.5">
            <Label htmlFor="link-title">Title</Label>
            <Input
              id="link-title"
              placeholder="Taken from the video"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </div>
          <div className="grid gap-1.5">
            <Label>Type</Label>
            <Select value={kind || 'none'} onValueChange={(v) => setKind(v === 'none' ? '' : v)}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="none">Type…</SelectItem>
                <SelectItem value="VL">VL</SelectItem>
                <SelectItem value="VO">VO</SelectItem>
                <SelectItem value="UE">UE</SelectItem>
                <SelectItem value="other">Other</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        <div className="grid gap-1.5">
          <Label>Subject</Label>
          <Select value={subject} onValueChange={setSubject}>
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {subjects.map((s) => (
                <SelectItem key={s.id} value={String(s.id)}>
                  {s.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <label className="flex items-center justify-between gap-3 text-sm">
          <span>
            Download the video
            <span className="block text-muted-foreground">Needed to find slides; audio only is smaller.</span>
          </span>
          <Switch checked={video} onCheckedChange={setVideo} />
        </label>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="outline"
            onClick={() => save.mutate()}
            disabled={!validUrl || live || save.isPending}
            title="Save the file to your Downloads folder without adding a lecture"
          >
            {save.isPending ? <Loader2 className="animate-spin" /> : <Download />} Just download
          </Button>
          <Button onClick={() => start.mutate()} disabled={!validUrl || live || start.isPending}>
            {start.isPending ? <Loader2 className="animate-spin" /> : <Link2 />} Import
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
