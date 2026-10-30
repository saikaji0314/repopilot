import React from 'react'
import { createRoot } from 'react-dom/client'
import { BaseStyles, ThemeProvider } from '@primer/react'
import '@primer/primitives/dist/css/functional/themes/light.css'
import '@fontsource-variable/mona-sans'
import '@fontsource/noto-sans-sc/400.css'
import '@fontsource/noto-sans-sc/500.css'
import '@fontsource/cascadia-mono/400.css'
import './styles.css'
import App from './App'

createRoot(document.getElementById('root')!).render(<React.StrictMode><ThemeProvider colorMode="day"><BaseStyles><App /></BaseStyles></ThemeProvider></React.StrictMode>)
