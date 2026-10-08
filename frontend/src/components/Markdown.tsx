import 'highlight.js/styles/atom-one-dark.css'
import 'katex/dist/katex.min.css'

import { createContext, isValidElement, useContext, type ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import rehypeHighlight from 'rehype-highlight'
import rehypeKatex from 'rehype-katex'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'

import { cn } from '@/lib/utils'

/** Plain text of rendered React children (for styling decisions). */
function nodeText(node: ReactNode): string {
  if (node == null || typeof node === 'boolean') return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(nodeText).join('')
  if (isValidElement<{ children?: ReactNode }>(node)) return nodeText(node.props.children)
  return ''
}

/** Lets timestamp chips anywhere inside a lecture page seek the player. */
export const SeekContext = createContext<((t: number) => void) | null>(null)

// [12:34], [1:02:03] and ranges like [12:34-13:10] (the chip seeks to the start).
const TS = /\[((?:\d{1,2}:)?\d{1,3}:\d{2})(?:\s*[-–]\s*((?:\d{1,2}:)?\d{1,3}:\d{2}))?\](?!\()/g

export function parseTs(text: string): number | null {
  const m = /^(?:(\d+):)?(\d{1,3}):(\d{2})$/.exec(text.trim())
  if (!m) return null
  return Number(m[1] ?? 0) * 3600 + Number(m[2]) * 60 + Number(m[3])
}

/** Turn bare [mm:ss] tokens into links (#t=seconds), leaving code blocks/spans alone. */
export function linkTimestamps(md: string): string {
  return md
    .split(/(```[\s\S]*?```|`[^`\n]*`)/g)
    .map((part, i) =>
      i % 2 === 1
        ? part
        : part.replace(TS, (whole, start: string) => {
            const t = parseTs(start)
            return t == null ? whole : `[${whole.slice(1, -1)}](#t=${t})`
          }),
    )
    .join('')
}

export function TimeChip({ t, children }: { t: number; children?: ReactNode }) {
  const seek = useContext(SeekContext)
  const label =
    children ??
    (t >= 3600
      ? `${Math.floor(t / 3600)}:${String(Math.floor((t % 3600) / 60)).padStart(2, '0')}:${String(Math.floor(t % 60)).padStart(2, '0')}`
      : `${String(Math.floor(t / 60)).padStart(2, '0')}:${String(Math.floor(t % 60)).padStart(2, '0')}`)
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation()
        seek?.(t)
      }}
      className="mx-0.5 inline-flex items-center rounded-md bg-primary/10 px-1.5 py-px align-baseline font-mono text-[0.78em] text-primary transition hover:bg-primary/20"
      title="Play from here"
    >
      {label}
    </button>
  )
}

export function Markdown({ children, className }: { children: string; className?: string }) {
  return (
    <div className={cn('prose-notes', className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex, [rehypeHighlight, { detect: true, ignoreMissing: true }]]}
        components={{
          a: ({ href, children: c }) => {
            const m = href?.match(/^#t=(\d+(?:\.\d+)?)$/)
            if (m) return <TimeChip t={Number(m[1])}>{c}</TimeChip>
            return (
              <a href={href} target="_blank" rel="noreferrer">
                {c}
              </a>
            )
          },
          blockquote: ({ children: c }) => {
            // "> **Added context:** ..." is styled differently from lecture content (SPEC §9).
            const text = nodeText(c).trim()
            const kind = text.startsWith('Added context')
              ? 'added-context'
              : text.startsWith('★')
                ? 'emphasis'
                : undefined
            return <blockquote className={kind}>{c}</blockquote>
          },
        }}
      >
        {linkTimestamps(children)}
      </ReactMarkdown>
    </div>
  )
}
