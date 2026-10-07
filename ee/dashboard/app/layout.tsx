// Proprietary: see ee/LICENSE
import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { DISCLAIMER } from "@/lib/format";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Tycheon Cloud", template: "%s | Tycheon Cloud" },
  description: "Calibrated forecasting and risk analytics.",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  colorScheme: "light dark",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body className="flex min-h-screen flex-col font-sans antialiased">
        <div className="flex-1">{children}</div>
        <footer className="border-t px-4 py-3 text-center text-xs muted" style={{ borderColor: "var(--border)" }}>
          {DISCLAIMER}
        </footer>
      </body>
    </html>
  );
}
