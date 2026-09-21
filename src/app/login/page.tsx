"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import LogoMark from "@/components/LogoMark";

const GOOGLE_ERRORS: Record<string, string> = {
  google_state: "انتهت صلاحية محاولة الدخول، حاول مرة أخرى",
  google_not_configured: "الدخول بقوقل غير مفعّل على هذا الخادم بعد",
  google_token: "تعذّر التحقق من حساب قوقل",
  google_userinfo: "تعذّر جلب بيانات حساب قوقل",
  account_disabled: "هذا الحساب معطَّل. تواصل مع الإدارة.",
};

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  );
}

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const code = searchParams.get("error");
    if (code) setError(GOOGLE_ERRORS[code] || "حدث خطأ أثناء الدخول بقوقل");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    const path = mode === "login" ? "/api/auth/login" : "/api/auth/register";
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password, name }),
    });
    setBusy(false);
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      setError(d.error || "حدث خطأ، حاول مرة أخرى");
      return;
    }
    router.push("/");
    router.refresh();
  }

  const field = "w-full rounded-full px-5 py-3 text-[15px] outline-none transition-colors";
  const fieldStyle = { background: "transparent", border: "1px solid var(--line)", color: "var(--ink)" };
  const pill = "flex items-center justify-center gap-2.5 w-full py-3 rounded-full text-[15px] font-medium no-underline transition-colors";

  return (
    <main className="min-h-screen grid lg:grid-cols-2" style={{ background: "var(--navy)" }}>
      {/* Form panel */}
      <div className="lh-glow flex flex-col items-center justify-center p-6 sm:p-10">
        <div className="w-full max-w-[380px]">
          <div className="flex items-center gap-2.5 mb-10">
            <LogoMark size={30} />
            <span className="text-lg font-bold">Local Hero</span>
          </div>

          <h1 className="lh-greeting text-[34px] sm:text-[40px] leading-[1.15] m-0 mb-3">
            ذكاء اصطناعي…
            <br />
            يبقى عندك
          </h1>
          <p className="text-[15px] mb-8" style={{ color: "var(--muted)" }}>
            بلا إنترنت، بلا سحابة، بلا تنازل عن خصوصيتك.
          </p>

          <div className="flex flex-col gap-2.5">
            <a href="/api/auth/google" className={pill} style={{ background: "var(--panel-2)", color: "var(--ink)" }}>
              <svg width="17" height="17" viewBox="0 0 18 18" aria-hidden="true">
                <path fill="#4285F4" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.9c1.7-1.57 2.7-3.87 2.7-6.62Z"/>
                <path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.9-2.26c-.8.54-1.83.86-3.06.86-2.35 0-4.34-1.59-5.05-3.72H.9v2.33A9 9 0 0 0 9 18Z"/>
                <path fill="#FBBC05" d="M3.95 10.7A5.4 5.4 0 0 1 3.67 9c0-.59.1-1.16.28-1.7V4.97H.9A9 9 0 0 0 0 9c0 1.45.35 2.83.9 4.03l3.05-2.33Z"/>
                <path fill="#EA4335" d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58C13.46.89 11.43 0 9 0A9 9 0 0 0 .9 4.97L3.95 7.3C4.66 5.17 6.65 3.58 9 3.58Z"/>
              </svg>
              المتابعة عبر قوقل
            </a>
          </div>

          <div className="flex items-center gap-3 my-6">
            <div className="flex-1 h-px" style={{ background: "var(--line)" }} />
            <span className="text-xs" style={{ color: "var(--muted)" }}>أو</span>
            <div className="flex-1 h-px" style={{ background: "var(--line)" }} />
          </div>

          <form onSubmit={submit} className="flex flex-col gap-2.5">
            {mode === "register" && (
              <input className={field} style={fieldStyle} placeholder="الاسم (اختياري)"
                value={name} onChange={(e) => setName(e.target.value)} />
            )}
            <input className={field} style={fieldStyle} type="email" placeholder="البريد الإلكتروني"
              value={email} onChange={(e) => setEmail(e.target.value)} required />
            <input className={field} style={fieldStyle} type="password" placeholder="كلمة المرور"
              value={password} onChange={(e) => setPassword(e.target.value)} required />

            {error && <p className="text-sm m-0 px-1" style={{ color: "#ff8b93" }}>{error}</p>}

            <button type="submit" disabled={busy}
              className="mt-1 py-3 rounded-full font-semibold text-white disabled:opacity-50 transition-opacity"
              style={{ background: "var(--crimson)" }}>
              {busy ? "..." : mode === "login" ? "المتابعة عبر البريد" : "إنشاء الحساب"}
            </button>
          </form>

          <p className="text-xs mt-6 text-center" style={{ color: "var(--muted)" }}>
            {mode === "login" ? "ما عندك حساب؟" : "عندك حساب أصلاً؟"}{" "}
            <button onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(""); }}
              className="font-semibold underline underline-offset-2" style={{ color: "var(--ink)" }}>
              {mode === "login" ? "أنشئ واحد" : "سجّل دخولك"}
            </button>
            {mode === "register" && <span> — أول حساب يصبح المدير تلقائياً.</span>}
          </p>
        </div>
      </div>

      {/* Visual panel — original abstract mark, not a stock photo or another product's imagery */}
      <div className="hidden lg:flex relative items-center justify-center overflow-hidden"
        style={{ background: "linear-gradient(160deg, #171e2e 0%, #1f273b 55%, #2a1620 100%)" }}>
        <svg className="absolute inset-0 w-full h-full" viewBox="0 0 800 900" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
          <circle cx="400" cy="40" r="340" fill="#e23744" opacity="0.20" />
          <circle cx="160" cy="720" r="220" fill="#e23744" opacity="0.08" />
          <g stroke="#3a4256" strokeWidth="1" opacity="0.5">
            <line x1="0" y1="300" x2="800" y2="300" />
            <line x1="0" y1="600" x2="800" y2="600" />
            <line x1="266" y1="0" x2="266" y2="900" />
            <line x1="533" y1="0" x2="533" y2="900" />
          </g>
        </svg>
        <div className="relative z-10 text-center px-10 max-w-[420px]">
          <div className="mx-auto mb-8" style={{ width: 96, height: 96 }}>
            <LogoMark size={96} />
          </div>
          <p className="lh-greeting text-2xl leading-relaxed" style={{ color: "#eef1f7" }}>
            كل محادثة، كل ملف، كل رد<br />ما يغادر جهازك أبداً.
          </p>
        </div>
        <div className="absolute bottom-8 flex items-center gap-4">
          <a href="https://www.linkedin.com/in/muath-al-mutairi-5211a0361/" target="_blank" rel="noopener noreferrer"
            className="flex items-center gap-1.5 text-sm no-underline" style={{ color: "#9aa3b8" }}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="/linkedin.png" alt="" width={16} height={16} className="rounded" />
            LinkedIn
          </a>
          <a href="https://github.com/Muath477" target="_blank" rel="noopener noreferrer"
            className="flex items-center gap-1.5 text-sm no-underline" style={{ color: "#9aa3b8" }}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="/github.png" alt="" width={16} height={16} className="rounded" />
            GitHub
          </a>
        </div>
      </div>
    </main>
  );
}
