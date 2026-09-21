"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import LogoMark from "@/components/LogoMark";
import { MoonIcon, SunIcon } from "@/components/Icons";

type User = { id: string; email: string; name: string | null; role: string; avatar: string | null };

function resizeImage(file: File, max: number): Promise<string> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const scale = Math.min(1, max / Math.max(img.width, img.height));
      const w = Math.round(img.width * scale);
      const h = Math.round(img.height * scale);
      const canvas = document.createElement("canvas");
      canvas.width = w;
      canvas.height = h;
      canvas.getContext("2d")!.drawImage(img, 0, 0, w, h);
      resolve(canvas.toDataURL("image/jpeg", 0.85));
    };
    img.onerror = reject;
    img.src = URL.createObjectURL(file);
  });
}

export default function Settings({ user }: { user: User }) {
  const router = useRouter();
  const [name, setName] = useState(user.name || "");
  const [avatar, setAvatar] = useState<string | null>(user.avatar);
  const [dark, setDark] = useState(false);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => setDark(document.documentElement.classList.contains("dark")), []);

  async function onPickAvatar(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    try { setAvatar(await resizeImage(f, 256)); setSaved(false); }
    catch { setError("تعذّر قراءة الصورة"); }
  }

  async function save() {
    setBusy(true); setError(""); setSaved(false);
    const res = await fetch("/api/profile", {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, avatar }),
    });
    setBusy(false);
    if (!res.ok) { const d = await res.json().catch(() => ({})); setError(d.error || "تعذّر الحفظ"); return; }
    setSaved(true); router.refresh();
  }

  function toggleTheme() {
    const n = !dark; setDark(n);
    document.documentElement.classList.toggle("dark", n);
    try { localStorage.setItem("lh-theme", n ? "dark" : "light"); } catch {}
  }

  async function logout() { await fetch("/api/auth/logout", { method: "POST" }); router.push("/login"); router.refresh(); }

  const section = "rounded-2xl p-5";
  const sectionStyle = { background: "var(--panel)", border: "1px solid var(--line)" };

  return (
    <div className="min-h-screen" style={{ background: "var(--navy)" }}>
      <header className="flex items-center gap-3 p-4" style={{ borderBottom: "1px solid var(--line)" }}>
        <LogoMark size={30} />
        <span className="font-extrabold text-lg">الإعدادات</span>
        <a href="/" className="ms-auto text-sm rounded-lg px-3 py-1.5"
          style={{ background: "var(--panel)", border: "1px solid var(--line)", color: "var(--ink)" }}>← رجوع للمحادثة</a>
      </header>

      <div className="max-w-2xl mx-auto p-6 flex flex-col gap-5">
        {/* Profile */}
        <section className={section} style={sectionStyle}>
          <h2 className="text-lg font-bold mb-4">الملف الشخصي</h2>
          <div className="flex items-center gap-4 mb-4">
            <div className="w-20 h-20 rounded-full overflow-hidden flex items-center justify-center shrink-0"
              style={{ background: "var(--panel-2)", border: "1px solid var(--line)" }}>
              {avatar
                ? <img src={avatar} alt="" className="w-full h-full object-cover" />
                : <span className="text-2xl font-bold" style={{ color: "var(--muted)" }}>{(name || user.email)[0]?.toUpperCase()}</span>}
            </div>
            <div>
              <label className="cursor-pointer text-sm rounded-lg px-3 py-2 inline-block"
                style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--ink)" }}>
                اختر صورة
                <input type="file" accept="image/*" className="hidden" onChange={onPickAvatar} />
              </label>
              {avatar && <button onClick={() => setAvatar(null)} className="text-sm ms-2" style={{ color: "var(--muted)" }}>إزالة</button>}
            </div>
          </div>

          <label className="block text-sm mb-1" style={{ color: "var(--muted)" }}>الاسم</label>
          <input value={name} onChange={(e) => setName(e.target.value)}
            className="w-full rounded-xl px-3 py-2.5 outline-none mb-3"
            style={{ background: "var(--navy)", border: "1px solid var(--line)", color: "var(--ink)" }} />

          <label className="block text-sm mb-1" style={{ color: "var(--muted)" }}>البريد الإلكتروني</label>
          <input value={user.email} disabled className="w-full rounded-xl px-3 py-2.5 outline-none opacity-60"
            style={{ background: "var(--navy)", border: "1px solid var(--line)", color: "var(--ink)" }} />

          {error && <p className="text-sm mt-3" style={{ color: "#e0503b" }}>{error}</p>}
          {saved && <p className="text-sm mt-3" style={{ color: "#4caf7d" }}>تم الحفظ ✓</p>}
          <button onClick={save} disabled={busy}
            className="mt-4 px-5 py-2.5 rounded-xl font-bold text-white disabled:opacity-50" style={{ background: "var(--crimson)" }}>
            {busy ? "..." : "حفظ"}
          </button>
        </section>

        {/* Appearance */}
        <section className={section} style={sectionStyle}>
          <h2 className="text-lg font-bold mb-3">المظهر</h2>
          <div className="flex items-center justify-between">
            <span style={{ color: "var(--muted)" }}>الثيم</span>
            <button onClick={toggleTheme} className="flex items-center gap-2 rounded-lg px-4 py-2 text-sm"
              style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--ink)" }}>
              {dark ? <MoonIcon size={15} /> : <SunIcon size={15} />}{dark ? "داكن" : "فاتح"}
            </button>
          </div>
        </section>

        {/* Account */}
        <section className={section} style={sectionStyle}>
          <h2 className="text-lg font-bold mb-3">الحساب</h2>
          <div className="flex items-center justify-between">
            <span style={{ color: "var(--muted)" }}>
              الدور: {user.role === "admin" ? "مدير (Admin)" : "مستخدم"}
            </span>
            <button onClick={logout} className="rounded-lg px-4 py-2 text-sm" style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--ink)" }}>
              تسجيل الخروج
            </button>
          </div>
        </section>
      </div>
    </div>
  );
}
