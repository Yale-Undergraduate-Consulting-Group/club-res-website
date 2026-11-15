import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
// Lato from the bundle (index.css): imported here so Vite resolves the font files.
import '@fontsource/lato/400.css'
import '@fontsource/lato/400-italic.css'
import '@fontsource/lato/700.css'
import '@fontsource/lato/700-italic.css'
import '@fontsource/lato/900.css'
import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
