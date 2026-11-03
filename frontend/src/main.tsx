import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './app.css'
import App from './App.tsx'
import { observeViewport } from './lib/viewport'
import { initializeInstallPrompt } from './lib/useInstallApp'

const queryClient = new QueryClient()
observeViewport()
initializeInstallPrompt()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
