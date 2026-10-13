import { useQuery } from '@tanstack/react-query'
import { Printer } from 'lucide-react'
import { useEffect } from 'react'
import { useParams } from 'react-router'

import { Markdown } from '@/components/Markdown'
import { cloze } from '@/components/materials/ItemLists'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import { formatDateTime, formatDuration } from '@/lib/dates'

/** Print pages always use the light theme, without the app shell. */
function usePrintTheme(title?: string) {
  useEffect(() => {
    const root = document.documentElement
    const wasDark = root.classList.contains('dark')
    root.classList.remove('dark')
    root.style.colorScheme = 'light'
    return () => {
      if (wasDark) root.classList.add('dark')
      root.style.colorScheme = ''
    }
  }, [])
  useEffect(() => {
    if (title) document.title = title
  }, [title])
}

function PrintButton() {
  return (
    <Button size="sm" variant="outline" className="print:hidden" onClick={() => window.print()}>
      <Printer /> Print / Save as PDF
    </Button>
  )
}

export function PrintLecture() {
  const id = Number(useParams().id)
  const { data: lecture } = useQuery({ queryKey: ['lecture', id], queryFn: () => api.lecture(id) })
  const { data: subjects = [] } = useQuery({ queryKey: ['subjects'], queryFn: api.subjects })
  const { data: m } = useQuery({ queryKey: ['materials', id], queryFn: () => api.materials(id) })
  usePrintTheme(lecture ? `L${lecture.number} ${lecture.title}` : undefined)
  if (!lecture || !m) return null
  const subject = subjects.find((s) => s.id === lecture.subject_id)
  const section = (title: string, md?: string) =>
    md?.trim() ? (
      <section className="print-section">
        <h2 className="print-h2">{title}</h2>
        <Markdown>{md}</Markdown>
      </section>
    ) : null

  return (
    <article className="print-page mx-auto max-w-3xl bg-white px-10 py-8 text-black">
      <header className="mb-6 flex items-start gap-4 border-b pb-4">
        <div className="flex-1">
          <div className="text-sm text-neutral-500">{subject?.name}</div>
          <h1 className="text-2xl font-semibold">
            L{lecture.number} {lecture.title}
          </h1>
          <div className="text-sm text-neutral-500">
            {formatDateTime(lecture.started_at)}
            {lecture.duration_s ? ` · ${formatDuration(lecture.duration_s)}` : ''}
          </div>
        </div>
        <PrintButton />
      </header>
      {section('Summary', m.notes.summary?.content_md)}
      {m.hints.length > 0 && (
        <section className="print-section">
          <h2 className="print-h2">Exam hints</h2>
          <ul className="list-disc space-y-1 pl-5 text-sm">
            {m.hints.map((h) => (
              <li key={h.id}>
                ★ [{formatDuration(h.start_s)}] {h.topic || h.quote}
              </li>
            ))}
          </ul>
        </section>
      )}
      {section('Notes', m.notes.notes?.content_md)}
      {m.explanations.map((e) => (
        <section key={e.id} className="print-section">
          <h2 className="print-h2">Explained: {e.title}</h2>
          <Markdown>{`${e.explanation_md}\n\n**Example.** ${e.example_md}\n\n**Check:** ${e.check_question}`}</Markdown>
        </section>
      ))}
      {section('Glossary', m.notes.glossary?.content_md)}
      {section('Announcements', m.notes.announcements?.content_md)}
      {m.flashcards.length > 0 && (
        <section className="print-section">
          <h2 className="print-h2">Flashcards</h2>
          <table className="w-full text-sm">
            <tbody>
              {m.flashcards.map((c) => (
                <tr key={c.id} className="border-b align-top">
                  <td className="w-1/2 py-1.5 pr-3">
                    <Markdown>{c.type === 'cloze' ? cloze(c.front, false) : c.front}</Markdown>
                  </td>
                  <td className="py-1.5">
                    <Markdown>{c.type === 'cloze' ? cloze(c.front, true) : c.back}</Markdown>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
      {m.questions.length > 0 && (
        <section className="print-section">
          <h2 className="print-h2">Exam questions</h2>
          <ol className="list-decimal space-y-3 pl-5">
            {m.questions.map((q) => (
              <li key={q.id}>
                <Markdown>{q.prompt}</Markdown>
                {q.options.length > 0 && (
                  <ul className="mt-1 list-[lower-alpha] pl-5 text-sm">
                    {q.options.map((o, i) => (
                      <li key={i}>{String((o as { text?: string }).text ?? '')}</li>
                    ))}
                  </ul>
                )}
              </li>
            ))}
          </ol>
          <h3 className="mt-6 font-semibold">Answers</h3>
          <ol className="list-decimal space-y-2 pl-5 text-sm">
            {m.questions.map((q) => (
              <li key={q.id}>
                <Markdown>{q.answer}</Markdown>
              </li>
            ))}
          </ol>
        </section>
      )}
    </article>
  )
}

export function PrintCheatSheet() {
  const id = Number(useParams().id)
  const { data: subject } = useQuery({ queryKey: ['subject', id], queryFn: () => api.subject(id) })
  const { data } = useQuery({ queryKey: ['cheatsheet', id], queryFn: () => api.cheatsheet(id) })
  const title = data?.note?.title || (subject ? `${subject.name}: cheat sheet` : '')
  usePrintTheme(title || undefined)
  if (!data?.note) return null
  return (
    <article className="cheatsheet mx-auto bg-white px-6 py-4 text-black">
      <header className="mb-2 flex items-center gap-3 border-b pb-1">
        <h1 className="flex-1 text-base font-semibold">{title}</h1>
        <PrintButton />
      </header>
      <div className="cheatsheet-columns">
        <Markdown>{data.note.content_md}</Markdown>
      </div>
    </article>
  )
}
