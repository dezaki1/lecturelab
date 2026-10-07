import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router'
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
import { Textarea } from '@/components/ui/textarea'
import { api } from '@/lib/api'
import type { Language, Subject, SubjectInput } from '@/lib/types'

const COLORS = ['#6366f1', '#0ea5e9', '#10b981', '#f59e0b', '#ef4444', '#ec4899', '#8b5cf6', '#64748b']

const empty: SubjectInput = {
  name: '',
  course_code: '',
  language: 'auto',
  output_language: 'en',
  lecturer: '',
  color: COLORS[0],
  exam_date: null,
  instructions: '',
  vocabulary: [],
}

export function SubjectDialog({
  open,
  onOpenChange,
  subject,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  subject?: Subject
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl max-h-[90vh] overflow-y-auto">
        {/* Mounted fresh each time the dialog opens, so the form starts from `subject`. */}
        <SubjectForm subject={subject} onDone={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  )
}

function SubjectForm({ subject, onDone }: { subject?: Subject; onDone: () => void }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [form, setForm] = useState<SubjectInput>(() => ({ ...empty, ...subject }))
  const [vocab, setVocab] = useState(() => (subject?.vocabulary ?? []).join('\n'))

  const save = useMutation({
    mutationFn: (data: SubjectInput) =>
      subject ? api.updateSubject(subject.id, data) : api.createSubject(data),
    onSuccess: (saved) => {
      qc.invalidateQueries({ queryKey: ['subjects'] })
      qc.setQueryData(['subject', saved.id], saved)
      toast.success(subject ? 'Subject saved' : `Created ${saved.name}`)
      onDone()
      if (!subject) navigate(`/subjects/${saved.id}`)
    },
    onError: (e: Error) => toast.error(e.message),
  })

  const set = <K extends keyof SubjectInput>(key: K, value: SubjectInput[K]) =>
    setForm((f) => ({ ...f, [key]: value }))

  const submit = (e: FormEvent) => {
    e.preventDefault()
    save.mutate({
      ...form,
      exam_date: form.exam_date || null,
      vocabulary: vocab
        .split(/[\n,;]/)
        .map((t) => t.trim())
        .filter(Boolean),
    })
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <DialogHeader>
        <DialogTitle>{subject ? 'Edit subject' : 'New subject'}</DialogTitle>
        <DialogDescription>
          Instructions and vocabulary are used for transcription and study material.
        </DialogDescription>
      </DialogHeader>

      <div className="grid gap-2">
        <Label htmlFor="name">Name</Label>
        <Input
          id="name"
          autoFocus
          required
          value={form.name}
          onChange={(e) => set('name', e.target.value)}
          placeholder="Reinforcement Learning"
        />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="grid gap-2">
          <Label htmlFor="code">Course code</Label>
          <Input id="code" value={form.course_code} onChange={(e) => set('course_code', e.target.value)} />
        </div>
        <div className="grid gap-2">
          <Label htmlFor="lecturer">Lecturer</Label>
          <Input id="lecturer" value={form.lecturer} onChange={(e) => set('lecturer', e.target.value)} />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="grid gap-2">
          <Label>Lecture language</Label>
          <Select value={form.language} onValueChange={(v) => set('language', v as Language)}>
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="auto">Auto-detect</SelectItem>
              <SelectItem value="en">English</SelectItem>
              <SelectItem value="de">German</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <div className="grid gap-2">
          <Label htmlFor="exam">Exam date</Label>
          <Input
            id="exam"
            type="date"
            value={form.exam_date ?? ''}
            onChange={(e) => set('exam_date', e.target.value || null)}
          />
        </div>
      </div>

      <div className="grid gap-2">
        <Label>Colour</Label>
        <div className="flex items-center gap-2">
          {COLORS.map((c) => (
            <button
              key={c}
              type="button"
              aria-label={`Colour ${c}`}
              onClick={() => set('color', c)}
              className={`size-6 rounded-full ring-offset-2 ring-offset-background transition ${form.color === c ? 'ring-2 ring-foreground' : ''}`}
              style={{ background: c }}
            />
          ))}
          <input
            type="color"
            aria-label="Custom colour"
            value={form.color}
            onChange={(e) => set('color', e.target.value)}
            className="size-7 cursor-pointer rounded border-0 bg-transparent p-0"
          />
        </div>
      </div>

      <div className="grid gap-2">
        <Label htmlFor="instructions">Custom instructions</Label>
        <Textarea
          id="instructions"
          rows={3}
          value={form.instructions}
          onChange={(e) => set('instructions', e.target.value)}
          placeholder="Math-heavy: show derivations step by step."
        />
      </div>

      <div className="grid gap-2">
        <Label htmlFor="vocab">Vocabulary</Label>
        <Textarea
          id="vocab"
          rows={3}
          value={vocab}
          onChange={(e) => setVocab(e.target.value)}
          placeholder={'One term per line, e.g.\nBellman equation\nQ-learning'}
        />
        <p className="text-xs text-muted-foreground">
          Technical terms and names. Whisper gets them as hints so they're spelled correctly.
        </p>
      </div>

      <DialogFooter>
        <Button type="button" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" disabled={save.isPending}>
          {subject ? 'Save' : 'Create'}
        </Button>
      </DialogFooter>
    </form>
  )
}
