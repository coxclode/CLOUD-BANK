import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "CLOUD BANK",
  description: "Evaluación de crédito personal — sistema multiagente",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es">
      <body className="min-h-screen bg-slate-50 text-slate-900 antialiased">
        <div className="mx-auto max-w-2xl px-4 py-10">{children}</div>
      </body>
    </html>
  );
}
