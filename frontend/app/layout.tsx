import type { ReactNode } from 'react';
import './globals.css';

export const metadata = {
  title: 'Project Titan v6.0',
  description: 'Local-first token scoring dashboard.'
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-background text-foreground">
        {children}
      </body>
    </html>
  );
}
