import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Headphones, Loader2 } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'

import { RecapPlayer } from '@/components/RecapPlayer'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useEvent } from '@/hooks/useEvents'
import { api } from '@/lib/api'

/** Lecture page tab: this lecture's audio recap, or a button to make one. */
export function LectureRecap({ lectureId, hasNotes }: { lectureId: number; hasNotes: boolean }) {
  const qc = useQueryClient()
  const key = ['recaps', { lecture: lectureId }]
  const { data: recaps = [] } = useQuery({
    queryKey: key,
    queryFn: () => api.recaps({ lecture_id: lectureId }),
  })
  const { data: voice } = useQuery({ queryKey: ['voice-settings'], queryFn: api.voiceSettings, retry: false })
  const [minutes, setMinutes] = useState('7')
  useEvent('recap', () => qc.invalidateQueries({ queryKey: ['recaps'] }))
  const make = useMutation({
    mutationFn: (m: number) => api.createRecap({ scope: 'lecture', lecture_id: lectureId, minutes: m }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['recaps'] }),
    onError: (e: Error) => toast.error(e.message),
  })
  const recap = recaps[0]

  if (recap) return <RecapPlayer recap={recap} onRemake={() => make.mutate(recap.minutes)} />
  return (
    <div className="space-y-3 py-2 text-sm">
      <p className="text-muted-foreground">
        A spoken summary of this lecture, to listen to on the way: the main ideas in order, what the lecturer
        stressed for the exam, and a few self-test questions at the end.
      </p>
      {voice && !voice.tts_ready ? (
        <p className="text-amber-600 dark:text-amber-400">
          Download a voice first in{' '}
          <Link to="/settings" className="underline">
            Settings → Voice
          </Link>
          .
        </p>
      ) : (
        <div className="flex items-center gap-2">
          <Select value={minutes} onValueChange={setMinutes}>
            <SelectTrigger size="sm" className="w-28" aria-label="Length">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {[5, 7, 10].map((m) => (
                <SelectItem key={m} value={String(m)}>
                  ~{m} min
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            size="sm"
            onClick={() => make.mutate(Number(minutes))}
            disabled={!hasNotes || make.isPending}
          >
            {make.isPending ? <Loader2 className="animate-spin" /> : <Headphones />} Make audio recap
          </Button>
          {!hasNotes && <span className="text-xs text-muted-foreground">Needs study notes first.</span>}
        </div>
      )}
    </div>
  )
}
