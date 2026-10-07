import { Badge } from '@/components/ui/badge'
import type { Lecture } from '@/lib/types'

export function LectureStatusBadge({ lecture }: { lecture: Lecture }) {
  switch (lecture.status) {
    case 'recording':
      return (
        <Badge className="bg-red-600 text-white">
          <span className="size-1.5 animate-pulse rounded-full bg-white" /> Recording
        </Badge>
      )
    case 'processing': {
      const pct = lecture.job_progress != null ? ` ${Math.round(lecture.job_progress * 100)}%` : ''
      return <Badge variant="secondary">{(lecture.job_message || 'Processing') + pct}</Badge>
    }
    case 'failed':
      return (
        <Badge variant="destructive" title={lecture.error ?? undefined}>
          Failed
        </Badge>
      )
    default:
      return null
  }
}
