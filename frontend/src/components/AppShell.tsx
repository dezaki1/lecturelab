import { useQuery } from '@tanstack/react-query'
import { GraduationCap, Home, Moon, Plus, Sun } from 'lucide-react'
import { useTheme } from 'next-themes'
import { useState } from 'react'
import { NavLink, Outlet } from 'react-router'

import { RecorderPanel } from '@/components/RecorderPanel'
import { SubjectDialog } from '@/components/SubjectDialog'
import { Button } from '@/components/ui/button'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'

export function AppShell() {
  const { data: subjects = [] } = useQuery({ queryKey: ['subjects'], queryFn: api.subjects })
  const [creating, setCreating] = useState(false)
  const { resolvedTheme, setTheme } = useTheme()

  const linkClass = ({ isActive }: { isActive: boolean }) =>
    cn(
      'flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors',
      isActive
        ? 'bg-sidebar-accent text-sidebar-accent-foreground font-medium'
        : 'text-sidebar-foreground/80 hover:bg-sidebar-accent/60',
    )

  return (
    <div className="flex h-screen bg-background text-foreground">
      <aside className="flex w-64 shrink-0 flex-col border-r bg-sidebar">
        <div className="flex items-center gap-2 px-4 py-4">
          <GraduationCap className="size-5 text-primary" />
          <span className="font-semibold tracking-tight">LectureLab</span>
        </div>

        <RecorderPanel />

        <nav className="px-2">
          <NavLink to="/" end className={linkClass}>
            <Home className="size-4" /> Overview
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
          <span className="text-xs text-muted-foreground">v0.1</span>
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

      <main className="flex-1 overflow-y-auto">
        <Outlet />
      </main>

      <SubjectDialog open={creating} onOpenChange={setCreating} />
    </div>
  )
}
