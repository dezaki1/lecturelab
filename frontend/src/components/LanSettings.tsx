import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy, Globe, KeyRound, Loader2, TabletSmartphone, TriangleAlert, XCircle } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { api } from '@/lib/api'
import type { LanStatus } from '@/lib/types'

/** Settings card: LAN mode for the tablet/phone, with the pairing QR code. */
export function LanSettings() {
  const qc = useQueryClient()
  const { data: s } = useQuery({ queryKey: ['lan'], queryFn: api.lan })
  const set = (d: LanStatus) => qc.setQueryData(['lan'], d)
  const onError = (e: Error) => toast.error(e.message)
  const toggle = useMutation({
    mutationFn: (enabled: boolean) => api.putLan({ enabled }),
    onSuccess: set,
    onError,
  })
  const rotate = useMutation({
    mutationFn: api.rotateLanToken,
    onSuccess: (d) => {
      set(d)
      toast.success('New pairing code: devices paired before have to scan again')
    },
    onError,
  })
  if (!s) return null

  return (
    <section className="mt-6 space-y-4 rounded-lg border bg-card p-5 text-sm">
      <div>
        <h2 className="flex items-center gap-2 font-semibold">
          <TabletSmartphone className="size-4" /> Tablet &amp; phone access
        </h2>
        <p className="mt-1 text-muted-foreground">
          Review cards, read notes and check your plan on a tablet or phone on the same Wi-Fi while this PC
          runs LectureLab. Only devices that scanned the pairing code get in; settings and recording stay on
          the PC. Use it on your home network, not on public Wi-Fi.
        </p>
      </div>
      <div className="flex items-center gap-2">
        <Switch
          id="lan-enabled"
          checked={s.enabled}
          disabled={toggle.isPending}
          onCheckedChange={(v) => toggle.mutate(v)}
        />
        <Label htmlFor="lan-enabled">Allow devices on my network</Label>
        {toggle.isPending && <Loader2 className="size-4 animate-spin text-muted-foreground" />}
      </div>
      {s.error && (
        <p className="flex items-center gap-1.5 text-destructive">
          <XCircle className="size-4" /> {s.error}
        </p>
      )}
      {s.running && (
        <div className="flex flex-wrap items-start gap-5">
          {s.qr_svg && (
            <div
              className="shrink-0 rounded-md bg-white [&_svg]:block [&_svg]:size-56"
              aria-label="Pairing QR code"
              role="img"
              dangerouslySetInnerHTML={{ __html: s.qr_svg }}
            />
          )}
          <div className="min-w-0 flex-1 space-y-2">
            {s.urls.length ? (
              <>
                {s.public_network && (
                  <p className="flex gap-1.5 rounded-md border border-amber-500/40 bg-amber-500/5 p-2 text-amber-700 dark:text-amber-300">
                    <TriangleAlert className="mt-0.5 size-4 shrink-0" />
                    <span>
                      This PC is on a public network (e.g. university Wi-Fi), not a home network. Such
                      networks usually block connections between devices, so pairing may not work, and other
                      people on it could reach this port (they would still need the pairing code). Use it at
                      home, or switch it off here.
                    </span>
                  </p>
                )}
                <p>
                  Scan the code with the tablet&apos;s camera, or open this link on it (it contains the
                  pairing code, so don&apos;t share it):
                </p>
                <div className="flex items-center gap-1">
                  <code className="min-w-0 truncate rounded bg-muted px-1.5 py-0.5 text-xs">
                    {s.pair_url}
                  </code>
                  <Button
                    size="icon-xs"
                    variant="ghost"
                    aria-label="Copy pairing link"
                    onClick={() =>
                      navigator.clipboard
                        .writeText(s.pair_url ?? '')
                        .then(() => toast.success('Pairing link copied'))
                        .catch(() => toast.error('Could not copy the link'))
                    }
                  >
                    <Copy />
                  </Button>
                </div>
                <p className="text-muted-foreground">
                  Afterwards the device opens <code>{s.urls[0]}</code> directly. In Chrome use{' '}
                  <b>⋮ → Add to home screen</b> for an app icon.
                  {s.urls.length > 1 && ` Other addresses of this PC: ${s.urls.slice(1).join(', ')}.`}
                </p>
                <p className="text-muted-foreground">
                  If the tablet can&apos;t connect, allow LectureLab (Python) on <i>private networks</i> when
                  Windows asks, or in Windows Defender Firewall.
                </p>
              </>
            ) : (
              <p className="text-amber-600 dark:text-amber-400">
                This PC has no address on a local network right now (connect to Wi-Fi or LAN).
              </p>
            )}
            <Button size="sm" variant="outline" onClick={() => rotate.mutate()} disabled={rotate.isPending}>
              <KeyRound /> New pairing code
            </Button>
          </div>
        </div>
      )}
      {s.running && (
        <div className="space-y-2 border-t pt-4">
          <h3 className="flex items-center gap-2 font-medium">
            <Globe className="size-4" /> Away from home (Tailscale)
          </h3>
          {s.tailscale_pair_url ? (
            <div className="flex flex-wrap items-start gap-5">
              {s.tailscale_qr_svg && (
                <div
                  className="shrink-0 rounded-md bg-white [&_svg]:block [&_svg]:size-44"
                  aria-label="Pairing QR code for Tailscale"
                  role="img"
                  dangerouslySetInnerHTML={{ __html: s.tailscale_qr_svg }}
                />
              )}
              <div className="min-w-0 flex-1 space-y-2">
                <p>
                  With Tailscale on (same account on both devices), the tablet reaches this PC from anywhere,
                  encrypted and only from your own devices. Scan this code or open the link once to pair:
                </p>
                <div className="flex items-center gap-1">
                  <code className="min-w-0 truncate rounded bg-muted px-1.5 py-0.5 text-xs">
                    {s.tailscale_pair_url}
                  </code>
                  <Button
                    size="icon-xs"
                    variant="ghost"
                    aria-label="Copy Tailscale pairing link"
                    onClick={() =>
                      navigator.clipboard
                        .writeText(s.tailscale_pair_url ?? '')
                        .then(() => toast.success('Pairing link copied'))
                        .catch(() => toast.error('Could not copy the link'))
                    }
                  >
                    <Copy />
                  </Button>
                </div>
                <p className="text-muted-foreground">
                  Then the tablet opens <code>{s.tailscale_url}</code>. If it loses its internet while
                  Tailscale is on, turn off <i>Use Tailscale DNS</i> in the Tailscale app&apos;s settings.
                </p>
              </div>
            </div>
          ) : (
            <p className="text-muted-foreground">
              To use LectureLab on the tablet away from home, install Tailscale (free) on this PC and on the
              tablet and sign in with the same account. Its address then appears here with a pairing code.
            </p>
          )}
        </div>
      )}
    </section>
  )
}
