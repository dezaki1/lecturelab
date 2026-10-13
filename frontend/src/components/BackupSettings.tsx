import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, HardDriveDownload, Loader2, XCircle } from 'lucide-react'
import { useEffect, useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { useEvent } from '@/hooks/useEvents'
import { api } from '@/lib/api'
import type { BackupStatus } from '@/lib/types'

const ago = (iso: string) => {
  const mins = (Date.now() - new Date(iso).getTime()) / 60_000
  if (mins < 2) return 'just now'
  if (mins < 120) return `${Math.round(mins)} min ago`
  if (mins < 48 * 60) return `${Math.round(mins / 60)} h ago`
  return `${Math.round(mins / 1440)} days ago`
}
const mb = (bytes: number) => `${(bytes / 1_000_000).toFixed(bytes < 10_000_000 ? 1 : 0)} MB`

/** Settings card: backup folder, schedule, run now, last result. */
export function BackupSettings() {
  const qc = useQueryClient()
  const { data: b } = useQuery({ queryKey: ['backup'], queryFn: api.backup })
  const [folder, setFolder] = useState('')
  useEffect(() => {
    if (b) setFolder(b.folder || b.suggested_folder)
  }, [b?.folder, b?.suggested_folder]) // only when the saved folder changes
  useEvent('backup', () => qc.invalidateQueries({ queryKey: ['backup'] }))
  const set = (d: BackupStatus) => qc.setQueryData(['backup'], d)
  const save = useMutation({
    mutationFn: (body: Parameters<typeof api.putBackup>[0]) => api.putBackup(body),
    onSuccess: set,
    onError: (e: Error) => toast.error(e.message),
  })
  const run = useMutation({
    mutationFn: api.runBackup,
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ['backup'] })
      toast.success(`Backed up: ${r.files_copied} new or changed files (${mb(r.bytes_copied)})`)
    },
    onError: (e: Error) => toast.error(e.message),
  })
  if (!b) return null
  const dirty = folder.trim() !== (b.folder || '')
  const last = b.last

  return (
    <section className="mt-6 space-y-4 rounded-lg border bg-card p-5 text-sm">
      <div>
        <h2 className="flex items-center gap-2 font-semibold">
          <HardDriveDownload className="size-4" /> Backup
        </h2>
        <p className="mt-1 text-muted-foreground">
          Copies the database and settings (a dated snapshot each time) and your lecture files (only what is
          new) to a folder. A folder inside OneDrive syncs to the cloud automatically. Your API key is not
          included.
        </p>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="backup-folder">Backup folder</Label>
        <div className="flex gap-2">
          <Input
            id="backup-folder"
            value={folder}
            onChange={(e) => setFolder(e.target.value)}
            spellCheck={false}
          />
          <Button
            variant="outline"
            disabled={!dirty || save.isPending}
            onClick={() => save.mutate({ folder: folder.trim() })}
          >
            Save
          </Button>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-4">
        <div className="flex items-center gap-2">
          <Switch
            id="backup-enabled"
            checked={b.enabled}
            disabled={!b.folder}
            onCheckedChange={(enabled) => save.mutate({ enabled })}
          />
          <Label htmlFor="backup-enabled">Back up automatically</Label>
        </div>
        <Select
          value={String(b.interval_hours)}
          onValueChange={(v) => save.mutate({ interval_hours: Number(v) })}
          disabled={!b.enabled}
        >
          <SelectTrigger size="sm" className="w-36">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="12">every 12 hours</SelectItem>
            <SelectItem value="24">daily</SelectItem>
            <SelectItem value="168">weekly</SelectItem>
          </SelectContent>
        </Select>
        <Button
          size="sm"
          variant="outline"
          className="ml-auto"
          disabled={!b.folder || b.running || run.isPending}
          onClick={() => run.mutate()}
        >
          {b.running || run.isPending ? <Loader2 className="animate-spin" /> : <HardDriveDownload />} Back up
          now
        </Button>
      </div>
      {last && (
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
          {last.ok ? (
            <CheckCircle2 className="size-3.5 text-emerald-500" />
          ) : (
            <XCircle className="size-3.5 text-destructive" />
          )}
          {last.ok
            ? `Last backup ${ago(last.at)} · ${last.files_copied} files copied · ${b.snapshots.length} snapshot${
                b.snapshots.length === 1 ? '' : 's'
              } kept`
            : `Last backup failed ${ago(last.at)}: ${last.error}`}
        </p>
      )}
    </section>
  )
}
