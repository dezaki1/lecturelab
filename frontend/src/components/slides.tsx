import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight, Loader2, Play, ScanText, Trash2 } from 'lucide-react'
import { createContext, useContext } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { api } from '@/lib/api'
import { formatDuration } from '@/lib/dates'
import type { Slide } from '@/lib/types'
import { cn } from '@/lib/utils'

/** The lecture's slides plus a way to open one in the viewer (provided by the lecture page). */
export const SlidesContext = createContext<{ slides: Slide[]; open: (index: number) => void } | null>(null)

/** Index of the slide on screen at time t: the last one that appeared at or before t. */
export function slideIndexAt(slides: Slide[], t: number): number {
  let lo = 0
  let hi = slides.length - 1
  let ans = -1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    if (slides[mid].t_s <= t) {
      ans = mid
      lo = mid + 1
    } else hi = mid - 1
  }
  return ans
}

export function SlideThumb({
  slide,
  onClick,
  className,
  showTime = true,
}: {
  slide: Slide
  onClick: () => void
  className?: string
  showTime?: boolean
}) {
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation()
        onClick()
      }}
      className={cn(
        'group relative block overflow-hidden rounded-md border bg-muted/40 transition hover:ring-2 hover:ring-primary/40',
        className,
      )}
      title={slide.ocr_text.split('\n')[0] || `Slide at ${formatDuration(slide.t_s)}`}
    >
      <img
        src={api.slideImageUrl(slide.id)}
        alt={slide.ocr_text.split('\n')[0] || 'Slide'}
        loading="lazy"
        className="aspect-video w-full object-contain"
      />
      {showTime && (
        <span className="absolute right-1 bottom-1 rounded bg-black/60 px-1 font-mono text-[10px] text-white">
          {formatDuration(slide.t_s)}
        </span>
      )}
    </button>
  )
}

/** Thumbnail of the slide on screen at time t, for note headings. Renders nothing elsewhere. */
export function SlideAt({ t }: { t: number }) {
  const ctx = useContext(SlidesContext)
  if (!ctx?.slides.length) return null
  // A note section's timestamp marks when the topic starts; the slide usually appears just after.
  const i = slideIndexAt(ctx.slides, t + 5)
  if (i < 0) return null
  return (
    <SlideThumb
      slide={ctx.slides[i]}
      onClick={() => ctx.open(i)}
      className="float-right mb-2 ml-4 w-56 max-sm:w-36"
    />
  )
}

export function SlideViewer({
  lectureId,
  slides,
  index,
  onIndex,
  onSeek,
}: {
  lectureId: number
  slides: Slide[]
  index: number | null
  onIndex: (i: number | null) => void
  onSeek: (t: number) => void
}) {
  const qc = useQueryClient()
  const del = useMutation({
    mutationFn: (id: number) => api.deleteSlide(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['slides', lectureId] })
      qc.invalidateQueries({ queryKey: ['lecture', lectureId] })
      if (index != null) onIndex(slides.length <= 1 ? null : Math.min(index, slides.length - 2))
    },
    onError: (e: Error) => toast.error(e.message),
  })
  const slide = index != null ? slides[index] : undefined
  const go = (d: number) => index != null && onIndex(Math.max(0, Math.min(slides.length - 1, index + d)))

  return (
    <Dialog open={slide != null} onOpenChange={(o) => !o && onIndex(null)}>
      <DialogContent
        className="gap-3 sm:max-w-5xl"
        onKeyDown={(e) => {
          if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
            e.stopPropagation() // don't also seek the audio player
            go(e.key === 'ArrowLeft' ? -1 : 1)
          }
        }}
      >
        {slide && (
          <>
            <DialogTitle className="pr-8 text-sm font-medium">
              Slide {index! + 1} of {slides.length} · {formatDuration(slide.t_s)}
            </DialogTitle>
            <DialogDescription className="sr-only">Slide image and its recognised text</DialogDescription>
            <img
              src={api.slideImageUrl(slide.id)}
              alt={slide.ocr_text.split('\n')[0] || 'Slide'}
              className="max-h-[70vh] w-full rounded-md border bg-muted/40 object-contain"
            />
            <div className="flex flex-wrap items-center gap-1">
              <Button size="sm" variant="outline" onClick={() => go(-1)} disabled={index === 0}>
                <ChevronLeft /> Previous
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => go(1)}
                disabled={index === slides.length - 1}
              >
                Next <ChevronRight />
              </Button>
              <Button
                size="sm"
                onClick={() => {
                  onSeek(slide.t_s)
                  onIndex(null)
                }}
              >
                <Play /> Play from here
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="ml-auto text-muted-foreground"
                onClick={() => del.mutate(slide.id)}
                disabled={del.isPending}
              >
                <Trash2 /> Delete slide
              </Button>
            </div>
            {slide.ocr_text && (
              <details className="text-xs text-muted-foreground">
                <summary className="cursor-pointer select-none">Slide text (OCR)</summary>
                <pre className="mt-2 max-h-40 overflow-auto font-sans whitespace-pre-wrap">
                  {slide.ocr_text}
                </pre>
              </details>
            )}
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}

export function SlidesGrid({
  lectureId,
  slides,
  stage,
  onOpen,
}: {
  lectureId: number
  slides: Slide[]
  stage?: string
  onOpen: (index: number) => void
}) {
  const qc = useQueryClient()
  const ocr = useMutation({
    mutationFn: () => api.rerunSlideOcr(lectureId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['lecture', lectureId] })
      toast.success('Reading the slides again')
    },
    onError: (e: Error) => toast.error(e.message),
  })

  if (!slides.length) {
    return (
      <p className="py-6 text-sm text-muted-foreground">
        No slides. While recording, LectureLab captures the Zoom window whenever the slide changes (the slides
        button next to the mic). Imported videos are scanned for slides automatically.
      </p>
    )
  }
  const reading = stage === 'pending' || stage === 'running'
  return (
    <div>
      <div className="mb-3 flex items-center gap-2 text-xs text-muted-foreground">
        {reading ? (
          <>
            <Loader2 className="size-3.5 animate-spin" /> Reading slide text…
          </>
        ) : stage === 'failed' ? (
          <span className="text-destructive">Reading the slide text failed.</span>
        ) : (
          <span>{slides.length} slides · click one to enlarge, or ← → to browse</span>
        )}
        <Button
          size="xs"
          variant="ghost"
          className="ml-auto"
          disabled={reading || ocr.isPending}
          onClick={() => ocr.mutate()}
        >
          <ScanText /> Read text again
        </Button>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {slides.map((s, i) => (
          <SlideThumb key={s.id} slide={s} onClick={() => onOpen(i)} />
        ))}
      </div>
    </div>
  )
}
