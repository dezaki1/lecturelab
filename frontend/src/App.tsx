import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from 'next-themes'
import { BrowserRouter, Route, Routes } from 'react-router'

import { AppShell } from '@/components/AppShell'
import { ImportProvider } from '@/components/ImportDialog'
import { LiveUpdates } from '@/components/LiveUpdates'
import { Toaster } from '@/components/ui/sonner'
import { TooltipProvider } from '@/components/ui/tooltip'
import { Home } from '@/pages/Home'
import { CardsPage } from '@/pages/CardsPage'
import { ExamPage } from '@/pages/ExamPage'
import { ExamsPage } from '@/pages/ExamsPage'
import { ChatPage } from '@/pages/ChatPage'
import { JobsPage } from '@/pages/JobsPage'
import { LecturePage } from '@/pages/LecturePage'
import { PracticePage } from '@/pages/PracticePage'
import { PlanPage } from '@/pages/PlanPage'
import { EvalsPage } from '@/pages/EvalsPage'
import { LivePage } from '@/pages/LivePage'
import { RecapsPage } from '@/pages/RecapsPage'
import { SpeakPage } from '@/pages/SpeakPage'
import { PrintCheatSheet, PrintLecture } from '@/pages/PrintPages'
import { ReviewPage } from '@/pages/ReviewPage'
import { SearchPage } from '@/pages/SearchPage'
import { SettingsPage } from '@/pages/SettingsPage'
import { UsagePage } from '@/pages/UsagePage'
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
            <ImportProvider>
              <Routes>
                <Route path="print/lectures/:id" element={<PrintLecture />} />
                <Route path="print/subjects/:id/cheatsheet" element={<PrintCheatSheet />} />
                <Route element={<AppShell />}>
                  <Route index element={<Home />} />
                  <Route path="subjects/:id" element={<SubjectPage />} />
                  <Route path="lectures/:id" element={<LecturePage />} />
                  <Route path="settings" element={<SettingsPage />} />
                  <Route path="jobs" element={<JobsPage />} />
                  <Route path="usage" element={<UsagePage />} />
                  <Route path="search" element={<SearchPage />} />
                  <Route path="review" element={<ReviewPage />} />
                  <Route path="cards" element={<CardsPage />} />
                  <Route path="exams" element={<ExamsPage />} />
                  <Route path="exams/:id" element={<ExamPage />} />
                  <Route path="practice" element={<PracticePage />} />
                  <Route path="plan" element={<PlanPage />} />
                  <Route path="speak" element={<SpeakPage />} />
                  <Route path="recaps" element={<RecapsPage />} />
                  <Route path="live" element={<LivePage />} />
                  <Route path="evals" element={<EvalsPage />} />
                  <Route path="speak/:id" element={<SpeakPage />} />
                  <Route path="chat" element={<ChatPage />} />
                  <Route path="chat/:id" element={<ChatPage />} />
                </Route>
              </Routes>
            </ImportProvider>
          </BrowserRouter>
          <LiveUpdates />
          <Toaster position="bottom-right" />
        </TooltipProvider>
      </QueryClientProvider>
    </ThemeProvider>
  )
}
