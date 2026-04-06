import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

// Reveal icon font glyphs only after Material Symbols has loaded,
// preventing a flash of icon-name text (e.g. "home", "search") on refresh.
if (document.fonts?.ready) {
  document.fonts.ready.then(() => {
    document.documentElement.classList.add('icons-loaded')
  })
} else {
  // Fallback for environments without the Font Loading API
  document.documentElement.classList.add('icons-loaded')
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
