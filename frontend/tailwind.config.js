/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Lato', 'system-ui', 'sans-serif'],
      },
      colors: {
        yale: {
          DEFAULT: '#00356b',
          light: '#286dc0',
          dark: '#002244',
        },
        accent: '#00356b',
        'accent-dark': '#002244',
        // Palette: Deep Navy → Pale Sky
        'deep-navy': '#1a2f5a',
        'mid-navy': '#1e3a6e',
        'slate-blue': '#3d5c82',
        'steel-blue': '#5b7fa6',
        'light-steel': '#8aaabf',
        'pale-sky': '#c8dced',
      },
    },
  },
  plugins: [],
}
