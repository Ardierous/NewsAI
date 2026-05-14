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
          <div>
            <Link href="/" style={{ fontSize: 24, fontWeight: 700, textDecoration: "none" }}>
              ExTellect Daily Digest
            </Link>
          </div>
          <p style={{ margin: "8px 0 0", fontSize: 14, color: "#64748b", lineHeight: 1.55 }}>
            <strong>Панель</strong> (<Link href="/">эта страница</Link>) — список всех выпусков по дате и кнопка «Создать или
            открыть сегодняшний». <strong>Мастер</strong> — по ссылке «Открыть мастер» или по прямому URL{" "}
            адрес вида <code style={{ fontSize: 13 }}>/digests/12</code> (число — id выпуска в базе): там шаги 0–4, расходы и кандидаты. Логотип сверху
            всегда возвращает на панель.
          </p>
        </header>
          {children}
        </div>
      </body>
    </html>
  );
}
