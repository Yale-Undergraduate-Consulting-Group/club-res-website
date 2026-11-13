/** @type {import('tailwindcss').Config} */
// Tailwind v4 does not read this file (nothing @config-imports it); the theme
// lives in src/styles/tokens.css. Kept in step with the YUCG palette so the two
// cannot disagree if a tool does read it.
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['YaleNew', 'Libre Caslon Text', 'Georgia', 'Times New Roman', 'serif'],
        chart: ['Lato', 'Helvetica Neue', 'Arial', 'sans-serif'],
      },
      colors: {
        yale: {
          DEFAULT: '#1c366a',
          light: '#3465a8',
          dark: '#0b1f3a',
        },
        accent: '#1c366a',
        'accent-dark': '#0b1f3a',
        'deep-navy': '#1c366a',
        'mid-navy': '#3465a8',
        'slate-blue': '#445469',
        'steel-blue': '#909ab1',
        'light-steel': '#c9ced6',
        'pale-sky': '#dfe6ef',
      },
      borderRadius: { DEFAULT: '0', sm: '0', md: '0', lg: '0', xl: '0', '2xl': '0', '3xl': '0', full: '0' },
      boxShadow: { DEFAULT: 'none', sm: 'none', md: 'none', lg: 'none', xl: 'none', '2xl': 'none' },
    },
  },
  plugins: [],
}
