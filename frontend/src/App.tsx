import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from 'next-themes'
import { BrowserRouter, Route, Routes } from 'react-router'

import { AppShell } from '@/components/AppShell'
import { LiveUpdates } from '@/components/LiveUpdates'
import { Toaster } from '@/components/ui/sonner'
import { TooltipProvider } from '@/components/ui/tooltip'
import { Home } from '@/pages/Home'
import { LecturePage } from '@/pages/LecturePage'
import { SubjectPage } from '@/pages/SubjectPage'

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } },
})

export default function App() {
  return (
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <BrowserRouter>
            <Routes>
              <Route element={<AppShell />}>
                <Route index element={<Home />} />
                <Route path="subjects/:id" element={<SubjectPage />} />
                <Route path="lectures/:id" element={<LecturePage />} />
              </Route>
            </Routes>
          </BrowserRouter>
          <LiveUpdates />
          <Toaster position="bottom-right" />
        </TooltipProvider>
      </QueryClientProvider>
    </ThemeProvider>
  )
}
