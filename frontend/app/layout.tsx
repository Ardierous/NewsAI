import "./globals.css";
import type { Metadata } from "next";
import Link from "next/link";
import { ReactNode } from "react";

export const metadata: Metadata = {
  title: "ExTellect Digest MVP",
  description: "AI digest wizard on CrewAI + ProxyAPI",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ru">
      <body>
        <div className="container">
          <header style={{ marginBottom: 20 }}>
            <Link href="/" style={{ fontSize: 24, fontWeight: 700, textDecoration: "none" }}>
              ExTellect Daily Digest
            </Link>
          </header>
          {children}
        </div>
      </body>
    </html>
  );
}
