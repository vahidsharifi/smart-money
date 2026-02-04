import type { Config } from 'tailwindcss';

const config: Config = {
  content: [
    './app/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}'
  ],
  theme: {
    extend: {
      colors: {
        background: '#0b0f17',
        foreground: '#e5e7eb',
        primary: '#4f46e5',
        muted: '#1f2937'
      }
    }
  },
  plugins: []
};

export default config;
