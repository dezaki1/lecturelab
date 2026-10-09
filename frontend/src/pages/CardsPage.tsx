import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ChevronLeft,
  ChevronRight,
  Download,
  Merge,
  Pause,
  Pencil,
  Play,
  RotateCcw,
  Search,
  Trash2,
  TriangleAlert,
} from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'

import { Markdown } from '@/components/Markdown'
import { cloze } from '@/components/materials/ItemLists'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { api } from '@/lib/api'
import type { CardQuery, CardRow, CardState } from '@/lib/types'
import { cn } from '@/lib/utils'

const PAGE = 50
const STATE_LABEL: Record<string, string> = {
  all: 'All cards',
  due: 'Due now',
  new: 'New',
  learning: 'Learning',
  review: 'Review',
  relearning: 'Relearning',
  suspended: 'Suspended',
  flagged: 'Flagged by fact-check',
}
const STATE_STYLE: Record<CardState, string> = {
  new: 'bg-sky-500/10 text-sky-700 dark:text-sky-300',
  learning: 'bg-amber-500/10 text-amber-700 dark:text-amber-300',
  relearning: 'bg-orange-500/10 text-orange-700 dark:text-orange-300',
  review: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300',
  suspended: 'bg-muted text-muted-foreground',
}

function dueLabel(card: CardRow): string {
  if (card.state === 'new' || !card.due) return ''
  const days = (new Date(card.due).getTime() - Date.now()) / 86_400_000
  if (days <= 0) return 'due now'
  if (days < 1) return `due in ${Math.max(1, Math.round(days * 24))} h`
  if (days < 60) return `due in ${Math.round(days)} d`
  return `due in ${Math.round(days / 30)} mo`
}

export function CardsPage() {
  return (
    <div className="mx-auto max-w-5xl px-8 py-10">
      <h1 className="text-2xl font-semibold tracking-tight">Cards</h1>
      <p className="mt-1 text-sm text-muted-foreground">
        All flashcards across your lectures. Edit, suspend, merge duplicates, or export to Anki.
      </p>
      <Tabs defaultValue="browse" className="mt-6">
        <TabsList>
          <TabsTrigger value="browse">Browse</TabsTrigger>
          <TabsTrigger value="duplicates">Duplicates</TabsTrigger>
        </TabsList>
        <TabsContent value="browse" className="mt-4">
          <Browser />
        </TabsContent>
        <TabsContent value="duplicates" className="mt-4">
          <Duplicates />
        </TabsContent>
      </Tabs>
    </div>
  )
}

function SubjectSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const { data: subjects = [] } = useQuery({ queryKey: ['subjects'], queryFn: api.subjects })
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger size="sm" className="w-56">
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
  )
}

function Browser() {
  const qc = useQueryClient()
  const [subject, setSubject] = useState('all')
  const [state, setState] = useState('all')
  const [sort, setSort] = useState<NonNullable<CardQuery['sort']>>('lecture')
  const [search, setSearch] = useState('')
  const [q, setQ] = useState('')
  const [page, setPage] = useState(0)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [editing, setEditing] = useState<CardRow | null>(null)

  useEffect(() => {
    const t = setTimeout(() => setQ(search.trim()), 250)
    return () => clearTimeout(t)
  }, [search])
  // A new filter starts from the first page with nothing selected.
  useEffect(() => {
    setPage(0)
    setSelected(new Set())
  }, [subject, state, sort, q])

  const filter: CardQuery = {
    subject_id: subject === 'all' ? undefined : Number(subject),
    state: state === 'all' ? undefined : state,
    q,
    sort,
  }
  const { data, isFetching } = useQuery({
    queryKey: ['cards', filter, page],
    queryFn: () => api.cards({ ...filter, offset: page * PAGE, limit: PAGE }),
    placeholderData: keepPreviousData,
  })
  const items = data?.items ?? []
  const total = data?.total ?? 0

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['cards'] })
    qc.invalidateQueries({ queryKey: ['review-summary'] })
    qc.invalidateQueries({ queryKey: ['dashboard'] })
  }
  const bulk = useMutation({
    mutationFn: ({ ids, action }: { ids: number[]; action: Parameters<typeof api.bulkCards>[1] }) =>
      api.bulkCards(ids, action),
    onSuccess: (r, v) => {
      refresh()
      setSelected(new Set())
      const verb = { suspend: 'Suspended', unsuspend: 'Unsuspended', delete: 'Deleted', forget: 'Reset' }[
        v.action
      ]
      toast.success(`${verb} ${r.changed} card${r.changed === 1 ? '' : 's'}`)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const toggle = (id: number) =>
    setSelected((s) => {
      const n = new Set(s)
      if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })
  const allOnPage = items.length > 0 && items.every((c) => selected.has(c.id))
  const ids = [...selected]
  const exportUrl = (format: 'apkg' | 'csv') =>
    api.exportCardsUrl(format, ids.length ? { ids } : { ...filter, sort: undefined })

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2">
        <SubjectSelect value={subject} onChange={setSubject} />
        <Select value={state} onValueChange={setState}>
          <SelectTrigger size="sm" className="w-48">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {Object.entries(STATE_LABEL).map(([k, v]) => (
              <SelectItem key={k} value={k}>
                {v}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={sort} onValueChange={(v) => setSort(v as typeof sort)}>
          <SelectTrigger size="sm" className="w-44">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="lecture">Newest lecture first</SelectItem>
            <SelectItem value="due">Due soonest</SelectItem>
            <SelectItem value="lapses">Most forgotten</SelectItem>
            <SelectItem value="newest">Newest card first</SelectItem>
          </SelectContent>
        </Select>
        <div className="relative min-w-48 flex-1">
          <Search className="absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search front and back"
            className="h-8 pl-8"
          />
        </div>
      </div>

      <div className="mt-3 flex min-h-8 flex-wrap items-center gap-2 text-sm text-muted-foreground">
        <input
          type="checkbox"
          className="size-4 accent-primary"
          aria-label="Select all on this page"
          checked={allOnPage}
          onChange={() =>
            setSelected((s) => {
              const n = new Set(s)
              for (const c of items) {
                if (allOnPage) n.delete(c.id)
                else n.add(c.id)
              }
              return n
            })
          }
        />
        {selected.size ? (
          <>
            <span className="font-medium text-foreground">{selected.size} selected</span>
            <Button size="xs" variant="outline" onClick={() => bulk.mutate({ ids, action: 'suspend' })}>
              <Pause /> Suspend
            </Button>
            <Button size="xs" variant="outline" onClick={() => bulk.mutate({ ids, action: 'unsuspend' })}>
              <Play /> Unsuspend
            </Button>
            <Button
              size="xs"
              variant="outline"
              title="Forget the review history: the cards start again as new"
              onClick={() => bulk.mutate({ ids, action: 'forget' })}
            >
              <RotateCcw /> Reset
            </Button>
            <Button size="xs" variant="outline" onClick={() => bulk.mutate({ ids, action: 'delete' })}>
              <Trash2 /> Delete
            </Button>
          </>
        ) : (
          <span>
            {total} card{total === 1 ? '' : 's'}
            {isFetching && ' …'}
          </span>
        )}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button size="xs" variant="outline" className="ml-auto" disabled={!total}>
              <Download /> Export{selected.size ? ` ${selected.size}` : ''}
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuLabel className="max-w-64 text-xs font-normal text-muted-foreground">
              {selected.size ? 'The selected cards' : 'All cards matching the filters'} (suspended ones left
              out)
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem asChild>
              <a href={exportUrl('apkg')} download>
                Anki deck (.apkg)
              </a>
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <a href={exportUrl('csv')} download>
                Spreadsheet (.csv)
              </a>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {items.length === 0 && !isFetching ? (
        <p className="py-10 text-center text-sm text-muted-foreground">No cards match.</p>
      ) : (
        <ul className="mt-2 divide-y rounded-lg border bg-card">
          {items.map((c) => (
            <CardLine
              key={c.id}
              card={c}
              selected={selected.has(c.id)}
              onSelect={() => toggle(c.id)}
              onEdit={() => setEditing(c)}
              onAction={(action) => bulk.mutate({ ids: [c.id], action })}
            />
          ))}
        </ul>
      )}

      {total > PAGE && (
        <div className="mt-3 flex items-center justify-end gap-2 text-sm text-muted-foreground">
          {page * PAGE + 1}–{Math.min(total, (page + 1) * PAGE)} of {total}
          <Button size="icon-sm" variant="ghost" disabled={page === 0} onClick={() => setPage(page - 1)}>
            <ChevronLeft />
          </Button>
          <Button
            size="icon-sm"
            variant="ghost"
            disabled={(page + 1) * PAGE >= total}
            onClick={() => setPage(page + 1)}
          >
            <ChevronRight />
          </Button>
        </div>
      )}

      <EditCard card={editing} onClose={() => setEditing(null)} onSaved={refresh} />
    </div>
  )
}

function CardLine({
  card,
  selected,
  onSelect,
  onEdit,
  onAction,
}: {
  card: CardRow
  selected: boolean
  onSelect: () => void
  onEdit: () => void
  onAction: (action: 'suspend' | 'unsuspend' | 'delete') => void
}) {
  const ref = card.source_refs[0]
  return (
    <li className={cn('group flex gap-3 px-4 py-3', selected && 'bg-primary/5')}>
      <input
        type="checkbox"
        className="mt-1 size-4 shrink-0 accent-primary"
        aria-label="Select card"
        checked={selected}
        onChange={onSelect}
      />
      <div className="min-w-0 flex-1">
        <div className="line-clamp-3 text-sm">
          <Markdown>{card.type === 'cloze' ? cloze(card.front, true) : card.front}</Markdown>
        </div>
        {card.back && (
          <div className="mt-1 line-clamp-2 text-sm text-muted-foreground">
            <Markdown>{card.back}</Markdown>
          </div>
        )}
        <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
          <span className={cn('rounded px-1.5 py-px font-medium capitalize', STATE_STYLE[card.state])}>
            {card.state}
          </span>
          {card.type !== 'basic' && <span>{card.type}</span>}
          <span className="flex items-center gap-1">
            <span className="size-2 rounded-full" style={{ background: card.subject_color }} />
            {card.lecture_id && card.lecture_number ? (
              <Link
                to={`/lectures/${card.lecture_id}${ref?.start_s != null ? `?t=${Math.floor(ref.start_s)}` : ''}`}
                className="hover:text-foreground hover:underline"
              >
                L{card.lecture_number} {card.lecture_title}
              </Link>
            ) : (
              card.subject_name
            )}
          </span>
          {dueLabel(card) && <span>{dueLabel(card)}</span>}
          {card.reps > 0 && (
            <span>
              {card.reps} review{card.reps === 1 ? '' : 's'}
              {card.lapses > 0 && `, forgotten ${card.lapses}×`}
            </span>
          )}
          {card.verified === 'flagged' && (
            <span
              className="flex items-center gap-1 text-amber-600 dark:text-amber-400"
              title={card.verify_note}
            >
              <TriangleAlert className="size-3" /> flagged
            </span>
          )}
          {card.tags.map((t) => (
            <Badge key={t} variant="secondary" className="h-4 px-1.5 text-[10px]">
              {t}
            </Badge>
          ))}
        </div>
      </div>
      <div className="flex shrink-0 items-start gap-0.5 opacity-60 transition group-hover:opacity-100">
        <Button size="icon-xs" variant="ghost" aria-label="Edit card" onClick={onEdit}>
          <Pencil />
        </Button>
        <Button
          size="icon-xs"
          variant="ghost"
          aria-label={card.suspended ? 'Unsuspend card' : 'Suspend card'}
          title={card.suspended ? 'Unsuspend' : 'Suspend: keep the card but leave it out of reviews'}
          onClick={() => onAction(card.suspended ? 'unsuspend' : 'suspend')}
        >
          {card.suspended ? <Play /> : <Pause />}
        </Button>
        <Button size="icon-xs" variant="ghost" aria-label="Delete card" onClick={() => onAction('delete')}>
          <Trash2 />
        </Button>
      </div>
    </li>
  )
}

function EditCard({
  card,
  onClose,
  onSaved,
}: {
  card: CardRow | null
  onClose: () => void
  onSaved: () => void
}) {
  const [front, setFront] = useState('')
  const [back, setBack] = useState('')
  const [type, setType] = useState('basic')
  const [tags, setTags] = useState('')
  useEffect(() => {
    if (!card) return
    setFront(card.front)
    setBack(card.back)
    setType(card.type)
    setTags(card.tags.join(', '))
  }, [card])
  const save = useMutation({
    mutationFn: () =>
      api.updateCard(card!.id, {
        front,
        back,
        type,
        tags: tags
          .split(',')
          .map((t) => t.trim())
          .filter(Boolean),
      }),
    onSuccess: () => {
      onSaved()
      onClose()
      toast.success('Card saved')
    },
    onError: (e: Error) => toast.error(e.message),
  })
  return (
    <Dialog open={card != null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Edit card</DialogTitle>
          <DialogDescription>
            Markdown and $math$ work. Cloze cards hide {'{{c1::the answer}}'}. Edited cards survive
            regeneration.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="card-front">Front</Label>
            <Textarea id="card-front" value={front} onChange={(e) => setFront(e.target.value)} rows={3} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="card-back">Back</Label>
            <Textarea id="card-back" value={back} onChange={(e) => setBack(e.target.value)} rows={4} />
          </div>
          <div className="flex gap-3">
            <div className="space-y-1.5">
              <Label>Type</Label>
              <Select value={type} onValueChange={setType}>
                <SelectTrigger size="sm" className="w-40">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="basic">Basic</SelectItem>
                  <SelectItem value="reversed">Both directions</SelectItem>
                  <SelectItem value="cloze">Cloze</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="flex-1 space-y-1.5">
              <Label htmlFor="card-tags">Tags (comma-separated)</Label>
              <Input id="card-tags" value={tags} onChange={(e) => setTags(e.target.value)} className="h-8" />
            </div>
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => save.mutate()} disabled={!front.trim() || save.isPending}>
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function Duplicates() {
  const qc = useQueryClient()
  const [subject, setSubject] = useState('all')
  const [dismissed, setDismissed] = useState<Set<string>>(new Set())
  const { data: groups, isFetching } = useQuery({
    queryKey: ['duplicates', subject],
    queryFn: () => api.duplicates(subject === 'all' ? undefined : Number(subject)),
  })
  const visible = useMemo(
    () => (groups ?? []).filter((g) => !dismissed.has(g.map((c) => c.id).join(','))),
    [groups, dismissed],
  )
  const merge = useMutation({
    mutationFn: ({ keep, others }: { keep: number; others: number[] }) => api.mergeCards(keep, others),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['duplicates'] })
      qc.invalidateQueries({ queryKey: ['cards'] })
      toast.success('Merged')
    },
    onError: (e: Error) => toast.error(e.message),
  })

  return (
    <div>
      <div className="flex items-center gap-3">
        <SubjectSelect value={subject} onChange={setSubject} />
        <span className="text-sm text-muted-foreground">
          {isFetching
            ? 'Looking for near-identical cards…'
            : `${visible.length} group${visible.length === 1 ? '' : 's'} of near-identical cards`}
        </span>
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        Merging keeps the card you pick, adds the others&apos; lecture links and tags to it, and keeps the
        review progress of whichever card you have practised most.
      </p>
      <div className="mt-4 space-y-4">
        {visible.map((g) => (
          <DuplicateGroup
            key={g.map((c) => c.id).join(',')}
            cards={g}
            busy={merge.isPending}
            onMerge={(keep) => merge.mutate({ keep, others: g.map((c) => c.id).filter((id) => id !== keep) })}
            onDismiss={() => setDismissed((d) => new Set(d).add(g.map((c) => c.id).join(',')))}
          />
        ))}
      </div>
    </div>
  )
}

function DuplicateGroup({
  cards,
  busy,
  onMerge,
  onDismiss,
}: {
  cards: CardRow[]
  busy: boolean
  onMerge: (keep: number) => void
  onDismiss: () => void
}) {
  const best = [...cards].sort(
    (a, b) => b.reps - a.reps || Number(b.edited_by_me) - Number(a.edited_by_me),
  )[0]
  const [keep, setKeep] = useState(best.id)
  return (
    <div className="rounded-lg border bg-card">
      <ul className="divide-y">
        {cards.map((c) => (
          <li key={c.id}>
            <label className="flex cursor-pointer gap-3 px-4 py-3">
              <input
                type="radio"
                className="mt-1 size-4 accent-primary"
                checked={keep === c.id}
                onChange={() => setKeep(c.id)}
              />
              <div className="min-w-0 flex-1 text-sm">
                <Markdown>{c.type === 'cloze' ? cloze(c.front, true) : c.front}</Markdown>
                {c.back && (
                  <div className="text-muted-foreground">
                    <Markdown>{c.back}</Markdown>
                  </div>
                )}
                <div className="mt-1 text-xs text-muted-foreground">
                  L{c.lecture_number} {c.lecture_title} · {c.state}
                  {c.reps > 0 && ` · ${c.reps} reviews`}
                  {c.edited_by_me && ' · edited by you'}
                </div>
              </div>
            </label>
          </li>
        ))}
      </ul>
      <div className="flex justify-end gap-2 border-t px-4 py-2">
        <Button size="sm" variant="ghost" onClick={onDismiss}>
          Not duplicates
        </Button>
        <Button size="sm" onClick={() => onMerge(keep)} disabled={busy}>
          <Merge /> Merge into the selected card
        </Button>
      </div>
    </div>
  )
}
