import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  BookOpen,
  GraduationCap,
  Layers,
  Lightbulb,
  Loader2,
  MessageSquarePlus,
  Send,
  Sparkles,
  Trash2,
} from 'lucide-react'
import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router'
import { toast } from 'sonner'

import { Markdown } from '@/components/Markdown'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { api, askStream } from '@/lib/api'
import type { ChatMessage, ChatThread } from '@/lib/types'
import { cn } from '@/lib/utils'

const QUICK = [
  {
    label: 'Explain simpler',
    icon: Lightbulb,
    prompt: 'Explain that more simply, as if to a first-year student.',
  },
  { label: 'Give an example', icon: BookOpen, prompt: 'Give me a concrete worked example of that.' },
  {
    label: 'Quiz me on this',
    icon: GraduationCap,
    prompt:
      'Quiz me on this: ask me one exam-style question about it and wait for my answer before revealing it.',
  },
]

export function ChatPage() {
  const id = useParams().id ? Number(useParams().id) : null
  const { data: threads = [] } = useQuery({ queryKey: ['threads'], queryFn: () => api.threads() })

  return (
    <div className="flex h-full">
      <aside className="flex w-64 shrink-0 flex-col border-r">
        <div className="p-3">
          <Button asChild variant="outline" size="sm" className="w-full">
            <Link to="/chat">
              <MessageSquarePlus /> New chat
            </Link>
          </Button>
        </div>
        <nav className="flex-1 space-y-0.5 overflow-y-auto px-2 pb-3">
          {threads.map((t) => (
            <Link
              key={t.id}
              to={`/chat/${t.id}`}
              className={cn(
                'block rounded-md px-2 py-1.5 text-sm transition-colors',
                t.id === id ? 'bg-muted font-medium' : 'hover:bg-muted/60',
              )}
            >
              <div className="truncate">{t.title}</div>
              <div className="truncate text-xs text-muted-foreground">{t.scope_label}</div>
            </Link>
          ))}
          {threads.length === 0 && <p className="px-2 text-sm text-muted-foreground">No chats yet.</p>}
        </nav>
      </aside>
      <section className="min-w-0 flex-1">{id ? <ThreadView key={id} id={id} /> : <NewChat />}</section>
    </div>
  )
}

function NewChat() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { data: subjects = [] } = useQuery({ queryKey: ['subjects'], queryFn: api.subjects })
  const [scope, setScope] = useState('all')
  const [grounded, setGrounded] = useState(true)
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [question, setQuestion] = useState('')

  const start = useMutation({
    mutationFn: () =>
      api.createThread({
        scope: scope === 'all' ? 'all' : 'subject',
        subject_id: scope === 'all' ? undefined : Number(scope),
        grounded,
        date_from: from || null,
        date_to: to || null,
      }),
    onSuccess: (t) => {
      qc.invalidateQueries({ queryKey: ['threads'] })
      navigate(`/chat/${t.id}`, { state: { ask: question.trim() } })
    },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <div className="mx-auto max-w-2xl px-4 sm:px-8 py-16">
      <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
        <Sparkles className="size-5 text-primary" /> Ask your lectures
      </h1>
      <p className="mt-1 text-sm text-muted-foreground">
        Answers cite the exact moment in your recordings. For one lecture, use “Ask” on the lecture page.
      </p>
      <div className="mt-6 grid gap-4 rounded-lg border bg-card p-4">
        <div className="grid grid-cols-2 gap-3">
          <div className="grid gap-1.5">
            <Label>Search in</Label>
            <Select value={scope} onValueChange={setScope}>
              <SelectTrigger className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All subjects</SelectItem>
                {subjects.map((s) => (
                  <SelectItem key={s.id} value={String(s.id)}>
                    {s.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-end gap-2 pb-1.5">
            <Switch id="grounded" checked={grounded} onCheckedChange={setGrounded} />
            <Label htmlFor="grounded" className="text-sm font-normal">
              Only from my lectures
            </Label>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div className="grid gap-1.5">
            <Label htmlFor="from">Lectures from</Label>
            <Input id="from" type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="to">to</Label>
            <Input id="to" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
          </div>
        </div>
        <Composer
          value={question}
          onChange={setQuestion}
          onSend={() => question.trim() && start.mutate()}
          busy={start.isPending}
          placeholder="e.g. How does the Bellman equation relate to value iteration?"
        />
      </div>
    </div>
  )
}

function Composer({
  value,
  onChange,
  onSend,
  busy,
  placeholder,
}: {
  value: string
  onChange: (v: string) => void
  onSend: () => void
  busy: boolean
  placeholder?: string
}) {
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (!busy) onSend()
    }
  }
  return (
    <div className="relative">
      <Textarea
        autoFocus
        rows={2}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKey}
        placeholder={placeholder ?? 'Ask a follow-up…  (Enter to send, Shift+Enter for a new line)'}
        className="resize-none pr-12"
      />
      <Button
        size="icon-sm"
        className="absolute right-2 bottom-2"
        aria-label="Send"
        disabled={busy || !value.trim()}
        onClick={onSend}
      >
        {busy ? <Loader2 className="animate-spin" /> : <Send />}
      </Button>
    </div>
  )
}

type Pending = { question: string; answer: string; sources: { label: string }[] } | null

function ThreadView({ id }: { id: number }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const { data: thread } = useQuery({ queryKey: ['thread', id], queryFn: () => api.thread(id) })
  const [draft, setDraft] = useState('')
  const [pending, setPending] = useState<Pending>(null)
  const bottom = useRef<HTMLDivElement>(null)
  const asked = useRef(false)
  const location = useLocation()

  const send = async (question: string) => {
    if (!question.trim() || pending) return
    setDraft('')
    setPending({ question, answer: '', sources: [] })
    try {
      await askStream(id, question, (e) => {
        if (e.type === 'delta') setPending((p) => p && { ...p, answer: p.answer + e.text })
        else if (e.type === 'sources') setPending((p) => p && { ...p, sources: e.sources })
        else if (e.type === 'error') toast.error(e.message)
      })
    } catch (err) {
      toast.error((err as Error).message)
    }
    await qc.invalidateQueries({ queryKey: ['thread', id] })
    qc.invalidateQueries({ queryKey: ['threads'] })
    setPending(null)
  }

  // A question typed on the "new chat" screen arrives via navigation state.
  useEffect(() => {
    const ask = (location.state as { ask?: string } | null)?.ask
    if (ask && !asked.current && thread && thread.messages.length === 0) {
      asked.current = true
      navigate(location.pathname, { replace: true, state: null })
      void send(ask)
    }
  })

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: 'end' })
  }, [thread?.messages.length, pending?.answer])

  const toggleGrounded = useMutation({
    mutationFn: (v: boolean) => api.updateThread(id, { grounded: v }),
    onSuccess: (t) => qc.setQueryData(['thread', id], t),
  })
  const remove = useMutation({
    mutationFn: () => api.deleteThread(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['threads'] })
      navigate('/chat')
    },
  })

  if (!thread) return null
  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-3 border-b px-6 py-3">
        <Layers className="size-4 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <div className="truncate font-medium">{thread.title}</div>
          <div className="truncate text-xs text-muted-foreground">
            {thread.scope_label}
            {(thread.date_from || thread.date_to) &&
              ` · ${thread.date_from ?? '…'} – ${thread.date_to ?? '…'}`}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Switch id="g" checked={thread.grounded} onCheckedChange={(v) => toggleGrounded.mutate(v)} />
          <Label htmlFor="g" className="text-xs font-normal text-muted-foreground">
            Only from my lectures
          </Label>
        </div>
        <Button variant="ghost" size="icon-sm" aria-label="Delete chat" onClick={() => remove.mutate()}>
          <Trash2 />
        </Button>
      </header>

      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl space-y-6 px-6 py-6">
          {thread.messages.map((m) => (
            <MessageView key={m.id} m={m} onQuick={send} busy={!!pending} />
          ))}
          {pending && (
            <>
              <UserBubble text={pending.question} />
              <div className="text-sm">
                {pending.answer ? (
                  <Markdown>{pending.answer}</Markdown>
                ) : (
                  <span className="flex items-center gap-2 text-muted-foreground">
                    <Loader2 className="size-4 animate-spin" />
                    {pending.sources.length
                      ? `Reading ${pending.sources.length} passages from your lectures…`
                      : 'Thinking…'}
                  </span>
                )}
              </div>
            </>
          )}
          <div ref={bottom} />
        </div>
      </div>

      <div className="border-t px-6 py-3">
        <div className="mx-auto max-w-3xl">
          <Composer value={draft} onChange={setDraft} onSend={() => send(draft)} busy={!!pending} />
        </div>
      </div>
    </div>
  )
}

function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-primary px-4 py-2 text-sm text-primary-foreground">
        {text}
      </div>
    </div>
  )
}

function MessageView({ m, onQuick, busy }: { m: ChatMessage; onQuick: (q: string) => void; busy: boolean }) {
  const cards = useMutation({
    mutationFn: () => api.flashcardsFromMessage(m.id),
    onSuccess: (r) => toast.success(`${r.created} flashcard${r.created === 1 ? '' : 's'} added`),
    onError: (e: Error) => toast.error(e.message),
  })
  if (m.role === 'user') return <UserBubble text={m.content} />
  if (m.error)
    return <p className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm">{m.error}</p>
  return (
    <div className="group">
      <Markdown className="text-sm" citations={m.citations}>
        {m.content}
      </Markdown>
      <div className="mt-2 flex flex-wrap gap-1 opacity-60 transition group-hover:opacity-100">
        <Button size="xs" variant="ghost" disabled={cards.isPending} onClick={() => cards.mutate()}>
          <Layers /> Make flashcards
        </Button>
        {QUICK.map((q) => (
          <Button key={q.label} size="xs" variant="ghost" disabled={busy} onClick={() => onQuick(q.prompt)}>
            <q.icon /> {q.label}
          </Button>
        ))}
      </div>
    </div>
  )
}

export function useStartChat() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: Parameters<typeof api.createThread>[0]) => api.createThread(data),
    onSuccess: (t: ChatThread) => {
      qc.invalidateQueries({ queryKey: ['threads'] })
      navigate(`/chat/${t.id}`)
    },
    onError: (e: Error) => toast.error(e.message),
  })
}
