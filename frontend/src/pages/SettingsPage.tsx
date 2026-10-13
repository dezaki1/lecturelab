import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, KeyRound, Loader2, PlugZap, Trash2, XCircle } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { toast } from 'sonner'

import { BackupSettings } from '@/components/BackupSettings'
import { LanSettings } from '@/components/LanSettings'
import { SlideCaptureSettings } from '@/components/SlideCaptureSettings'
import { TimetableSettings } from '@/components/TimetableSettings'
import { VoiceSettings } from '@/components/VoiceSettings'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { api } from '@/lib/api'

const PROVIDER_LABEL: Record<string, string> = {
  anthropic: 'Anthropic Claude (best quality)',
  ollama: 'Ollama (local, offline)',
  openai: 'OpenAI-compatible endpoint',
  mock: 'Mock (no AI, for testing the UI)',
}

export function SettingsPage() {
  const qc = useQueryClient()
  const { data: s } = useQuery({ queryKey: ['llm-settings'], queryFn: api.llmSettings })
  const { data: system } = useQuery({ queryKey: ['system'], queryFn: api.system })
  const [key, setKey] = useState('')
  const [test, setTest] = useState<{ ok: boolean; error?: string; model?: string } | null>(null)

  const refresh = (d?: unknown) => {
    if (d && typeof d === 'object' && 'provider' in d) qc.setQueryData(['llm-settings'], d)
    else qc.invalidateQueries({ queryKey: ['llm-settings'] })
  }
  const saveKey = useMutation({
    mutationFn: () => api.setApiKey(key),
    onSuccess: () => {
      setKey('')
      setTest(null)
      refresh()
      toast.success('API key saved to Windows Credential Manager')
    },
    onError: (e: Error) => toast.error(e.message),
  })
  const removeKey = useMutation({ mutationFn: () => api.deleteApiKey(), onSuccess: () => refresh() })
  const provider = useMutation({ mutationFn: api.setProvider, onSuccess: refresh })
  const runTest = useMutation({ mutationFn: api.testLlm, onSuccess: setTest })

  if (system?.remote)
    return (
      <div className="mx-auto max-w-3xl px-4 py-10 sm:px-8">
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="mt-4 text-sm text-muted-foreground">
          Settings are only available on the PC running LectureLab.
        </p>
      </div>
    )
  if (!s) return null
  const k = s.anthropic.key

  return (
    <div className="mx-auto max-w-3xl px-4 sm:px-8 py-10">
      <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>

      <section className="mt-8 space-y-4 rounded-lg border bg-card p-5">
        <div>
          <h2 className="font-semibold">AI provider</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Used for summaries, notes, flashcards, questions and chat. Switch to Ollama to work offline.
          </p>
        </div>
        <Select value={s.provider} onValueChange={(v) => provider.mutate(v)}>
          <SelectTrigger className="w-80">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {s.providers.map((p) => (
              <SelectItem key={p} value={p}>
                {PROVIDER_LABEL[p] ?? p}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {s.provider === 'anthropic' && (
          <p className="text-sm text-muted-foreground">
            Study material: <code>{s.anthropic.main}</code> (effort {s.anthropic.main_effort}) · checks:{' '}
            <code>{s.anthropic.cheap}</code>
          </p>
        )}
        {s.provider === 'ollama' && (
          <p className="text-sm text-muted-foreground">
            Model <code>{s.ollama.model}</code> at <code>{s.ollama.url}</code>. Lower quality and slower, but
            nothing leaves your computer.
          </p>
        )}
        <div className="flex items-center gap-3">
          <Button variant="outline" size="sm" onClick={() => runTest.mutate()} disabled={runTest.isPending}>
            {runTest.isPending ? <Loader2 className="animate-spin" /> : <PlugZap />} Test connection
          </Button>
          {test &&
            (test.ok ? (
              <span className="flex items-center gap-1 text-sm text-emerald-600 dark:text-emerald-400">
                <CheckCircle2 className="size-4" /> Works ({test.model})
              </span>
            ) : (
              <span className="flex items-center gap-1 text-sm text-destructive">
                <XCircle className="size-4" /> {test.error}
              </span>
            ))}
        </div>
      </section>

      <section className="mt-6 space-y-4 rounded-lg border bg-card p-5">
        <div>
          <h2 className="flex items-center gap-2 font-semibold">
            <KeyRound className="size-4" /> Anthropic API key
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Create one at console.anthropic.com (billed separately from a Claude subscription). It is stored
            in Windows Credential Manager, never in files or logs, and only ever sent to Anthropic.
          </p>
        </div>
        {k.set ? (
          <div className="flex items-center gap-3 text-sm">
            <CheckCircle2 className="size-4 text-emerald-500" />
            <span>
              Key saved <code>{k.hint}</code>
              {k.source === 'env' && ' (from the ANTHROPIC_API_KEY environment variable)'}
            </span>
            {k.source !== 'env' && (
              <Button variant="ghost" size="sm" onClick={() => removeKey.mutate()}>
                <Trash2 /> Remove
              </Button>
            )}
          </div>
        ) : (
          <p className="text-sm text-amber-600 dark:text-amber-400">No key saved yet.</p>
        )}
        <form
          className="flex gap-2"
          onSubmit={(e: FormEvent) => {
            e.preventDefault()
            if (key.trim()) saveKey.mutate()
          }}
        >
          <div className="grid flex-1 gap-1.5">
            <Label htmlFor="apikey" className="sr-only">
              API key
            </Label>
            <Input
              id="apikey"
              type="password"
              autoComplete="off"
              spellCheck={false}
              placeholder={k.set ? 'Replace key: sk-ant-…' : 'sk-ant-…'}
              value={key}
              onChange={(e) => setKey(e.target.value)}
            />
          </div>
          <Button type="submit" disabled={!key.trim() || saveKey.isPending}>
            Save key
          </Button>
        </form>
      </section>

      <SlideCaptureSettings />

      <TimetableSettings />

      <VoiceSettings />

      <BackupSettings />

      <LanSettings />

      <section className="mt-6 flex items-center gap-4 rounded-lg border bg-card p-5 text-sm">
        <div className="flex-1">
          <h2 className="font-semibold">Export</h2>
          <p className="mt-1 text-muted-foreground">
            All subjects as an Obsidian vault (Markdown with wikilinks, flashcards for the Spaced Repetition
            plugin, the concept maps as Mermaid). Lecture PDFs and cheat sheets are on the lecture and subject
            pages.
          </p>
        </div>
        <Button variant="outline" asChild>
          <a href={api.vaultUrl()} download>
            Download vault
          </a>
        </Button>
      </section>

      {system && (
        <section className="mt-6 rounded-lg border bg-card p-5 text-sm text-muted-foreground">
          Data folder: <code>{system.data_dir}</code> · {system.free_gb} GB free. Advanced options (models,
          effort, hotkeys, transcription) are in <code>config.toml</code> there.
        </section>
      )}
    </div>
  )
}
