import { useQuery } from '@tanstack/react-query'
import {
  CalendarCheck,
  Coins,
  FlaskConical,
  GraduationCap,
  Headphones,
  ListChecks,
  Home,
  Layers,
  Library,
  Menu,
  Mic,
  Radio,
  NotebookPen,
  MessageSquare,
  Moon,
  Plus,
  Search,
  Settings,
  Sun,
  Target,
} from 'lucide-react'
import { useTheme } from 'next-themes'
import { useEffect, useRef, useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router'

import { RecorderPanel } from '@/components/RecorderPanel'
import { SubjectDialog } from '@/components/SubjectDialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'

export function AppShell() {
  const { data: subjects = [] } = useQuery({ queryKey: ['subjects'], queryFn: api.subjects })
  const { data: review } = useQuery({
    queryKey: ['review-summary'],
    queryFn: api.reviewSummary,
    refetchInterval: 60_000,
  })
  const toReview = review ? review.due + review.new : 0
  const { data: deadlines } = useQuery({ queryKey: ['deadlines'], queryFn: api.deadlines })
  const suggested = deadlines?.suggested.length ?? 0
  const { data: jobCounts } = useQuery({
    queryKey: ['job-counts'],
    queryFn: api.jobCounts,
    refetchInterval: 30_000,
  })
  const activeJobs = jobCounts ? jobCounts.queued + jobCounts.running : 0
  const [creating, setCreating] = useState(false)
  const { data: system } = useQuery({ queryKey: ['system'], queryFn: api.system })
  const remote = system?.remote ?? false // opened from a tablet/phone (LAN mode)
  // Narrow screens: the sidebar is a drawer, closed again after every navigation.
  const [navOpen, setNavOpen] = useState(false)
  const location = useLocation()
  useEffect(() => setNavOpen(false), [location.pathname, location.search])
  const { resolvedTheme, setTheme } = useTheme()
  const navigate = useNavigate()
  const searchRef = useRef<HTMLInputElement>(null)
  const [q, setQ] = useState('')

  // Ctrl+K focuses search from anywhere.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() === 'k' && (e.ctrlKey || e.metaKey)) {
        e.preventDefault()
        searchRef.current?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const linkClass = ({ isActive }: { isActive: boolean }) =>
    cn(
      'flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors',
      isActive
        ? 'bg-sidebar-accent text-sidebar-accent-foreground font-medium'
        : 'text-sidebar-foreground/80 hover:bg-sidebar-accent/60',
    )

  return (
    <div className="flex h-dvh bg-background text-foreground">
      {navOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/40 md:hidden"
          aria-hidden
          onClick={() => setNavOpen(false)}
        />
      )}
      <aside
        className={cn(
          'fixed inset-y-0 left-0 z-50 flex w-64 shrink-0 flex-col border-r bg-sidebar transition-transform md:static md:translate-x-0',
          navOpen ? 'translate-x-0 shadow-xl' : '-translate-x-full',
        )}
      >
        <div className="flex items-center gap-2 px-4 py-4">
          <GraduationCap className="size-5 text-primary" />
          <span className="font-semibold tracking-tight">LectureLab</span>
        </div>

        {!remote && <RecorderPanel />}

        <form
          className="relative mx-2 mb-2"
          onSubmit={(e) => {
            e.preventDefault()
            if (q.trim()) navigate(`/search?q=${encodeURIComponent(q.trim())}`)
          }}
        >
          <Search className="absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            ref={searchRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search lectures  (Ctrl+K)"
            className="h-8 pl-8 text-sm"
          />
        </form>

        <nav className="space-y-0.5 px-2">
          <NavLink to="/" end className={linkClass}>
            <Home className="size-4" /> Overview
          </NavLink>
          <NavLink to="/review" className={linkClass}>
            <Layers className="size-4" /> Review
            {toReview > 0 && (
              <span className="ml-auto rounded-full bg-primary px-1.5 text-[11px] font-medium text-primary-foreground">
                {toReview}
              </span>
            )}
          </NavLink>
          <NavLink to="/cards" className={linkClass}>
            <Library className="size-4" /> Cards
          </NavLink>
          <NavLink to="/practice" className={linkClass}>
            <Target className="size-4" /> Practice
          </NavLink>
          <NavLink to="/plan" className={linkClass}>
            <CalendarCheck className="size-4" /> Plan
            {suggested > 0 && (
              <span className="ml-auto rounded-full bg-amber-500/20 px-1.5 text-[11px] font-medium text-amber-700 dark:text-amber-300">
                {suggested}
              </span>
            )}
          </NavLink>
          <NavLink to="/exams" className={linkClass}>
            <NotebookPen className="size-4" /> Exam simulator
          </NavLink>
          <NavLink to="/live" className={linkClass}>
            <Radio className="size-4" /> Live lecture
          </NavLink>
          <NavLink to="/speak" className={linkClass}>
            <Mic className="size-4" /> Oral practice
          </NavLink>
          <NavLink to="/recaps" className={linkClass}>
            <Headphones className="size-4" /> Audio recaps
          </NavLink>
          <NavLink to="/chat" className={linkClass}>
            <MessageSquare className="size-4" /> Ask your lectures
          </NavLink>
        </nav>

        <div className="mt-4 flex items-center justify-between px-4 pb-1">
          <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">Subjects</span>
          <Button size="icon-xs" variant="ghost" aria-label="New subject" onClick={() => setCreating(true)}>
            <Plus />
          </Button>
        </div>
        <nav className="flex-1 space-y-0.5 overflow-y-auto px-2 pb-4">
          {subjects.map((s) => (
            <NavLink key={s.id} to={`/subjects/${s.id}`} className={linkClass} title={s.name}>
              <span className="size-2.5 shrink-0 rounded-full" style={{ background: s.color }} />
              <span className="truncate">{s.name}</span>
              {s.lecture_count > 0 && (
                <span className="ml-auto text-xs text-muted-foreground">{s.lecture_count}</span>
              )}
            </NavLink>
          ))}
          {subjects.length === 0 && (
            <p className="px-2 py-1 text-sm text-muted-foreground">No subjects yet.</p>
          )}
        </nav>

        <div className="flex items-center justify-between border-t px-3 py-2">
          <div className="flex flex-col gap-0.5">
            <NavLink to="/jobs" className={linkClass}>
              <ListChecks className="size-4" /> Jobs
              {activeJobs > 0 && (
                <span className="ml-auto flex items-center gap-1 text-[11px] text-muted-foreground">
                  <span className="size-1.5 animate-pulse rounded-full bg-primary" />
                  {activeJobs}
                </span>
              )}
              {activeJobs === 0 && (jobCounts?.failed_recent ?? 0) > 0 && (
                <span
                  className="ml-auto size-1.5 rounded-full bg-destructive"
                  title="A job failed recently"
                />
              )}
            </NavLink>
            <NavLink to="/usage" className={linkClass}>
              <Coins className="size-4" /> Usage
            </NavLink>
            <NavLink to="/evals" className={linkClass}>
              <FlaskConical className="size-4" /> Evals
            </NavLink>
            <NavLink to="/settings" className={linkClass}>
              <Settings className="size-4" /> Settings
            </NavLink>
          </div>
          <Button
            size="icon-sm"
            variant="ghost"
            aria-label="Toggle dark mode"
            onClick={() => setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')}
          >
            {resolvedTheme === 'dark' ? <Sun /> : <Moon />}
          </Button>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-2 border-b px-2 py-1.5 pt-[max(0.375rem,env(safe-area-inset-top))] md:hidden">
          <Button size="icon-sm" variant="ghost" aria-label="Open menu" onClick={() => setNavOpen(true)}>
            <Menu />
          </Button>
          <GraduationCap className="size-4 text-primary" />
          <span className="text-sm font-semibold tracking-tight">LectureLab</span>
          {toReview > 0 && (
            <NavLink
              to="/review"
              className="ml-auto rounded-full bg-primary px-2 py-0.5 text-xs font-medium text-primary-foreground"
            >
              {toReview} to review
            </NavLink>
          )}
        </header>
        <main className="flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>

      <SubjectDialog open={creating} onOpenChange={setCreating} />
    </div>
  )
}
