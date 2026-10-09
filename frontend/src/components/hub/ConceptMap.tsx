import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router'

import type { ConceptMapData } from '@/lib/types'

const CHARS_PER_LINE = 20
const LINE_H = 15
const PAD_X = 10
const PAD_Y = 8

function wrap(label: string): string[] {
  const lines: string[] = []
  let cur = ''
  for (const w of label.split(/\s+/)) {
    if (cur && (cur + ' ' + w).length > CHARS_PER_LINE) {
      lines.push(cur)
      cur = w
    } else cur = cur ? `${cur} ${w}` : w
  }
  if (cur) lines.push(cur)
  return lines.slice(0, 3)
}

interface Laid {
  nodes: {
    id: string
    x: number
    y: number
    w: number
    h: number
    lines: string[]
    lecture: number
    lectureId: number | null
  }[]
  edges: { key: string; points: { x: number; y: number }[]; label: string; lx: number; ly: number }[]
  width: number
  height: number
}

/** Concept map laid out with dagre (loaded on demand); nodes open the lecture that introduced them. */
export function ConceptMap({ data }: { data: ConceptMapData }) {
  const navigate = useNavigate()
  const [laid, setLaid] = useState<Laid | null>(null)
  const [actualSize, setActualSize] = useState(false)
  const lectures = useMemo(() => [...new Set(data.nodes.map((n) => n.lecture))].sort((a, b) => a - b), [data])

  useEffect(() => {
    let cancelled = false
    void import('@dagrejs/dagre').then((dagre) => {
      const g = new dagre.graphlib.Graph({ multigraph: true })
      g.setGraph({ rankdir: 'TB', nodesep: 26, ranksep: 56, edgesep: 12, marginx: 8, marginy: 8 })
      g.setDefaultEdgeLabel(() => ({}))
      for (const n of data.nodes) {
        const lines = wrap(n.label)
        const w = Math.max(...lines.map((l) => l.length)) * 7 + PAD_X * 2
        g.setNode(n.id, { width: w, height: lines.length * LINE_H + PAD_Y * 2, lines, node: n })
      }
      data.edges.forEach((e, i) =>
        g.setEdge(
          e.source,
          e.target,
          { label: e.label, width: e.label.length * 5.6, height: 12, labelpos: 'c' },
          `e${i}`,
        ),
      )
      dagre.layout(g)
      if (cancelled) return
      const graph = g.graph()
      setLaid({
        width: graph.width ?? 0,
        height: graph.height ?? 0,
        nodes: g.nodes().map((id) => {
          const v = g.node(id) as unknown as {
            x: number
            y: number
            width: number
            height: number
            lines: string[]
            node: ConceptMapData['nodes'][number]
          }
          return {
            id,
            x: v.x,
            y: v.y,
            w: v.width,
            h: v.height,
            lines: v.lines,
            lecture: v.node.lecture,
            lectureId: v.node.lecture_id,
          }
        }),
        edges: g.edges().map((e) => {
          const v = g.edge(e) as unknown as {
            points: { x: number; y: number }[]
            label: string
            x: number
            y: number
          }
          return { key: e.name ?? `${e.v}-${e.w}`, points: v.points, label: v.label, lx: v.x, ly: v.y }
        }),
      })
    })
    return () => {
      cancelled = true
    }
  }, [data])

  if (!data.nodes.length) return null
  if (!laid) return <p className="py-6 text-sm text-muted-foreground">Drawing the map…</p>
  const hue = (lecture: number) => {
    const i = lectures.indexOf(lecture)
    return `oklch(0.7 0.13 ${(i * 360) / Math.max(lectures.length, 1) + 200})`
  }
  return (
    <div>
      <div className="mb-2 flex flex-wrap gap-3 text-xs text-muted-foreground">
        {lectures.map((l) => (
          <span key={l} className="flex items-center gap-1">
            <span className="size-2.5 rounded-full" style={{ background: hue(l) }} /> L{l}
          </span>
        ))}
        <span className="ml-auto">Click a concept to open the lecture that introduced it.</span>
        <button
          type="button"
          className="text-primary hover:underline"
          onClick={() => setActualSize(!actualSize)}
        >
          {actualSize ? 'Fit to width' : 'Actual size'}
        </button>
      </div>
      <div className="overflow-auto rounded-lg border bg-card">
        <svg
          width={actualSize ? laid.width : '100%'}
          height={actualSize ? laid.height : undefined}
          style={actualSize ? undefined : { maxWidth: laid.width }}
          viewBox={`0 0 ${laid.width} ${laid.height}`}
          className="mx-auto block max-w-none text-foreground"
          role="img"
          aria-label="Concept map"
        >
          <defs>
            <marker
              id="arrow"
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="7"
              markerHeight="7"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" className="fill-muted-foreground" />
            </marker>
          </defs>
          {laid.edges.map((e) => (
            <g key={e.key}>
              <path
                d={`M ${e.points.map((p) => `${p.x} ${p.y}`).join(' L ')}`}
                className="fill-none stroke-muted-foreground/60"
                strokeWidth={1.2}
                markerEnd="url(#arrow)"
              />
              {e.label && (
                <text x={e.lx} y={e.ly + 3} textAnchor="middle" className="fill-muted-foreground text-[10px]">
                  {e.label}
                </text>
              )}
            </g>
          ))}
          {laid.nodes.map((n) => (
            <g
              key={n.id}
              transform={`translate(${n.x - n.w / 2} ${n.y - n.h / 2})`}
              className={n.lectureId ? 'cursor-pointer' : undefined}
              onClick={() => n.lectureId && navigate(`/lectures/${n.lectureId}`)}
            >
              <title>{`${n.lines.join(' ')} (introduced in L${n.lecture})`}</title>
              <rect
                width={n.w}
                height={n.h}
                rx={7}
                className="fill-background"
                stroke={hue(n.lecture)}
                strokeWidth={1.8}
              />
              {n.lines.map((line, i) => (
                <text
                  key={i}
                  x={n.w / 2}
                  y={PAD_Y + LINE_H * (i + 1) - 3}
                  textAnchor="middle"
                  className="fill-current text-[12px]"
                >
                  {line}
                </text>
              ))}
            </g>
          ))}
        </svg>
      </div>
    </div>
  )
}
