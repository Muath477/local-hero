import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Local Hero",
  description: "منصّة محادثة محلّية خاصّة — كل شيء على جهازك.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ar" dir="rtl" className="h-full" suppressHydrationWarning>
      <head>
        <script
          dangerouslySetInnerHTML={{
            // Dark is the default look (matches the logo's own navy background);
            // only an explicit saved "light" choice opts back out of it.
            __html: `try{if(localStorage.getItem('lh-theme')!=='light')document.documentElement.classList.add('dark')}catch(e){}`,
          }}
        />
      </head>
      <body className="min-h-full">{children}</body>
    </html>
  );
}
