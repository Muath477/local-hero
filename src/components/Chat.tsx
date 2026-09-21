"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import LogoMark from "@/components/LogoMark";
import { ChartIcon, FileTextIcon, GearIcon, MoonIcon, PencilIcon, SunIcon, TrashIcon } from "@/components/Icons";

type User = { id: string; email: string; name: string | null; role: string; avatar?: string | null };
type Model = { id: string; name?: string; owned_by?: string; description?: string };
type ChatSummary = { id: string; title: string; model: string };
type Attachment = { id: string; name: string };
type Msg = { id?: string; role: string; content: string; attachment?: Attachment | null };

const LANGUAGES = ["Arabic", "English", "French", "Spanish", "German", "Turkish", "Italian", "Russian", "Chinese", "Japanese", "Hindi", "Urdu", "Persian", "Portuguese"];
const LANG_AR: Record<string, string> = {
  Arabic: "العربية", English: "الإنجليزية", French: "الفرنسية", Spanish: "الإسبانية", German: "الألمانية",
  Turkish: "التركية", Italian: "الإيطالية", Russian: "الروسية", Chinese: "الصينية", Japanese: "اليابانية",
  Hindi: "الهندية", Urdu: "الأردية", Persian: "الفارسية", Portuguese: "البرتغالية",
};
export default function Chat({ user }: { user: User }) {
  const router = useRouter();
  const [models, setModels] = useState<Model[]>([]);
  const [model, setModel] = useState("auto");
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [activeChatId, setActiveChatId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [lang, setLang] = useState("Arabic");
  const [error, setError] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const [dark, setDark] = useState(false);
  const [editingChatId, setEditingChatId] = useState<string | null>(null);
  const [editingChatTitle, setEditingChatTitle] = useState("");
  const [editingMsgIndex, setEditingMsgIndex] = useState<number | null>(null);
  const [editingMsgContent, setEditingMsgContent] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);

  const agents = models.filter((m) => m.owned_by !== "ollama");
  const localModels = models.filter((m) => m.owned_by === "ollama");
  const currentName = models.find((m) => m.id === model)?.name || model;
  const name = user.name || user.email.split("@")[0];
  const hour = new Date().getHours();
  const greeting = hour < 12 ? "صباح الخير" : "مساء الخير";

  useEffect(() => {
    fetch("/api/models").then((r) => r.json()).then((d) => setModels(d.models ?? []));
    refreshChats();
    setDark(document.documentElement.classList.contains("dark"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);

  // Presence for the admin dashboard's "online now" — a tab open in this
  // app pings every 30s; the server treats a ~90s-old ping as still online.
  useEffect(() => {
    const ping = () => { fetch("/api/heartbeat", { method: "POST" }).catch(() => {}); };
    ping();
    const id = setInterval(ping, 30_000);
    return () => clearInterval(id);
  }, []);

  // Guard against a stale tab: the session cookie is per-browser, not
  // per-tab, so logging into a different account in another tab (or this
  // one) silently swaps who *new* requests from this tab act as, while
  // the already-rendered chat list/messages on screen keep showing the
  // previous account's data until something forces a refetch. Checking
  // identity on focus and hard-reloading on a mismatch re-renders the
  // whole page server-side for whichever account is actually signed in
  // now, instead of trying to patch client state around the swap.
  useEffect(() => {
    async function checkSession() {
      try {
        const r = await fetch("/api/auth/me");
        const d = await r.json();
        if (d.user?.id !== user.id) window.location.reload();
      } catch {
        // network hiccup — not evidence of a session change, ignore
      }
    }
    window.addEventListener("focus", checkSession);
    document.addEventListener("visibilitychange", checkSession);
    return () => {
      window.removeEventListener("focus", checkSession);
      document.removeEventListener("visibilitychange", checkSession);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function toggleTheme() {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    try { localStorage.setItem("lh-theme", next ? "dark" : "light"); } catch {}
  }

  async function refreshChats() { const d = await fetch("/api/chats").then((r) => r.json()); setChats(d.chats ?? []); }
  async function openChat(id: string) {
    const d = await fetch(`/api/chats/${id}`).then((r) => r.json());
    if (d.chat) { setActiveChatId(id); setModel(d.chat.model); setMessages(d.chat.messages); }
  }
  function newChat() { setActiveChatId(null); setMessages([]); setFile(null); setError(""); }

  async function send() {
    if (busy) return;
    setError("");
    if (file) {
      if (isRagFile(file)) {
        const uploaded = await uploadForRag();
        if (!uploaded || !input.trim()) return;
        // A question was already typed alongside the file — ask it now
        // that the file is indexed, instead of making the user hit send twice.
      } else {
        return translateFile();
      }
    }
    const content = input.trim();
    if (!content) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", content }, { role: "assistant", content: "" }]);
    setBusy(true);
    try {
      const res = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ chatId: activeChatId, model, content }) });
      const newId = res.headers.get("X-Chat-Id");
      if (newId && !activeChatId) setActiveChatId(newId);
      if (!res.body) throw new Error("no stream");
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value, { stream: true });
        setMessages((m) => { const c = [...m]; c[c.length - 1] = { role: "assistant", content: c[c.length - 1].content + chunk }; return c; });
      }
    } catch {
      setMessages((m) => { const c = [...m]; c[c.length - 1] = { role: "assistant", content: "تعذّر الحصول على رد. تأكد أن خدمة الموديل تعمل." }; return c; });
    } finally { setBusy(false); refreshChats(); }
  }

  // Any attached file that isn't .docx goes to the file-analyst (RAG) path
  // instead of Tarjuman's translate-only flow — the user can drop in any
  // format at all, this just decides which of the two existing pipelines
  // handles it. The backend (tools/file_reader.py) is the one that
  // actually knows whether a given file is text-readable.
  function isRagFile(f: File) { return !f.name.toLowerCase().endsWith(".docx"); }

  async function uploadForRag(): Promise<boolean> {
    if (!file) return false;
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch("/api/upload", { method: "POST", body: fd });
      const d = await res.json();
      if (!res.ok) { setError(d.error || "فشل رفع الملف"); return false; }
      setFile(null);
      setModel("file-analyst");
      if (!input.trim()) {
        setMessages((m) => [...m, { role: "assistant", content: `📄 تم رفع "${d.filename}" وتقسيمه لـ ${d.chunks_indexed} مقطع — اسأل عنه الآن.` }]);
      }
      return true;
    } catch { setError("تعذّر الاتصال بخدمة رفع الملفات"); return false; }
    finally { setBusy(false); }
  }

  async function translateFile() {
    if (!file) return;
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", file); fd.append("targetLanguage", lang);
      if (activeChatId) fd.append("chatId", activeChatId);
      const res = await fetch("/api/translate", { method: "POST", body: fd });
      const d = await res.json();
      if (!res.ok) { setError(d.error || "فشلت الترجمة"); return; }
      setFile(null); await openChat(d.chatId); refreshChats();
    } catch { setError("تعذّر الاتصال بخدمة الترجمة"); } finally { setBusy(false); }
  }

  async function renameChat(id: string) {
    const title = editingChatTitle.trim();
    setEditingChatId(null);
    if (!title) return;
    await fetch(`/api/chats/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title }) });
    refreshChats();
  }

  async function deleteChat(id: string) {
    if (!confirm("حذف هذه المحادثة نهائياً؟")) return;
    await fetch(`/api/chats/${id}`, { method: "DELETE" });
    if (id === activeChatId) newChat();
    refreshChats();
  }

  // Edit a past user message: truncate everything after it and regenerate,
  // same standard chat-app semantics the API route implements.
  async function saveMessageEdit(msgId: string | undefined, index: number) {
    const content = editingMsgContent.trim();
    setEditingMsgIndex(null);
    if (!content || !msgId || !activeChatId) return;

    setMessages((m) => [
      ...m.slice(0, index + 1).map((msg, i) => (i === index ? { ...msg, content } : msg)),
      { role: "assistant", content: "" },
    ]);
    setBusy(true);
    try {
      const res = await fetch(`/api/chats/${activeChatId}/messages/${msgId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      if (!res.body) throw new Error("no stream");
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value, { stream: true });
        setMessages((m) => { const c = [...m]; c[c.length - 1] = { role: "assistant", content: c[c.length - 1].content + chunk }; return c; });
      }
    } catch {
      setMessages((m) => { const c = [...m]; c[c.length - 1] = { role: "assistant", content: "تعذّر الحصول على رد." }; return c; });
    } finally { setBusy(false); refreshChats(); }
  }

  async function logout() { await fetch("/api/auth/logout", { method: "POST" }); router.push("/login"); router.refresh(); }

  const menuItem = (m: Model) => (
    <button key={m.id} onClick={() => { setModel(m.id); setMenuOpen(false); }}
      className="w-full flex items-start justify-between gap-2 rounded-lg px-3 py-2 text-right"
      style={{ background: m.id === model ? "var(--panel-2)" : "transparent", color: "var(--ink)" }}>
      <span className="min-w-0">
        <span className="block text-sm font-medium" dir="auto">{m.name || m.id}</span>
        <span className="block text-xs truncate" style={{ color: "var(--muted)" }} dir="auto">
          {m.description || (m.owned_by === "ollama" ? "موديل محلي" : m.id)}
        </span>
      </span>
      {m.id === model && <span className="mt-1" style={{ color: "var(--crimson)" }}>✓</span>}
    </button>
  );

  const composer = (
    <div className="relative">
      {error && <p className="text-sm mb-2 text-center" style={{ color: "#e0503b" }}>{error}</p>}
      <div className="rounded-3xl px-3 py-2" style={{ background: "var(--panel)", border: "1px solid color-mix(in srgb, var(--ink) 8%, transparent)" }}>
        {file && (
          <div className="flex items-center gap-2 flex-wrap mb-2 text-sm">
            <span className="flex items-center gap-1.5"><FileTextIcon size={14} style={{ color: "var(--muted)" }} />{file.name}</span>
            <button onClick={() => setFile(null)} style={{ color: "var(--muted)" }}>✕</button>
            {!isRagFile(file) && (
              <>
                <span className="ms-auto" style={{ color: "var(--muted)" }}>ترجم إلى:</span>
                <select value={lang} onChange={(e) => setLang(e.target.value)} className="rounded-lg px-2 py-1 outline-none"
                  style={{ background: "var(--navy)", border: "1px solid var(--line)", color: "var(--ink)" }}>
                  {LANGUAGES.map((l) => <option key={l} value={l}>{LANG_AR[l] || l}</option>)}
                </select>
              </>
            )}
          </div>
        )}
        <div className="flex items-start gap-2">
          <span className="mt-1 w-5 h-5 rounded-full flex items-center justify-center text-[11px] font-bold shrink-0"
            style={{ background: "var(--panel-2)", color: "var(--muted)" }} aria-hidden="true">؟</span>
          <textarea ref={taRef} value={input} onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
            rows={1} disabled={!!file && !isRagFile(file)}
            placeholder={file ? (isRagFile(file) ? "اسأل عن الملف (اختياري)…" : "اضغط ترجم…") : "كيف أقدر أساعدك اليوم؟"}
            className="flex-1 min-w-0 resize-none bg-transparent outline-none px-1 pt-0.5 pb-1 max-h-48 disabled:opacity-50"
            style={{ color: "var(--ink)" }} />
        </div>
        <div className="flex items-center gap-2">
          <label className="cursor-pointer rounded-full w-8 h-8 flex items-center justify-center text-lg shrink-0"
            title="إرفاق أي ملف — docx يُترجم، غيره يُحلَّل" style={{ border: "1px solid var(--line)", color: "var(--muted)" }}>
            ＋
            <input type="file" className="hidden" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setError(""); }} />
          </label>
          <button onClick={() => setMenuOpen((v) => !v)} className="flex items-center gap-1.5 rounded-full px-3 h-8 text-sm"
            style={{ border: "1px solid var(--line)", color: "var(--ink)" }}>
            <LogoMark size={13} /> <span className="font-medium">{currentName}</span> <span style={{ color: "var(--muted)" }}>▾</span>
          </button>
          <div className="flex-1" />
          <button onClick={send} disabled={busy}
            className="rounded-full h-9 min-w-9 px-3 flex items-center justify-center font-bold text-white shrink-0 disabled:opacity-40"
            style={{ background: "var(--crimson)" }}>
            {busy ? "…" : file ? (isRagFile(file) ? "رفع" : "ترجم") : "↑"}
          </button>
        </div>
      </div>
      {menuOpen && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setMenuOpen(false)} />
          <div className="absolute z-20 bottom-full mb-2 rounded-2xl p-2 w-72 shadow-2xl"
            style={{ background: "var(--panel)", border: "1px solid var(--line)" }}>
            <p className="px-3 py-1 text-xs font-medium" style={{ color: "var(--muted)" }}>الوكلاء</p>
            {agents.map(menuItem)}
            {localModels.length > 0 && <p className="px-3 py-1 mt-1 text-xs font-medium" style={{ color: "var(--muted)" }}>الموديلات المحلية</p>}
            <div className="max-h-56 overflow-y-auto">{localModels.map(menuItem)}</div>
          </div>
        </>
      )}
    </div>
  );

  return (
    <div className="flex h-screen">
      {/* Sidebar */}
      <aside className="w-72 shrink-0 flex flex-col" style={{ background: "var(--panel)" }}>
        <div className="flex items-center gap-2 p-4">
          <LogoMark size={30} />
          <span className="font-extrabold text-lg">Local Hero</span>
        </div>
        <button onClick={newChat} className="mx-3 mb-3 py-2.5 rounded-xl font-semibold flex items-center justify-center gap-2"
          style={{ background: "var(--panel-2)", border: "1px solid var(--line)", color: "var(--ink)" }}>
          <span style={{ color: "var(--crimson)" }}>＋</span> محادثة جديدة
        </button>
        <div className="flex-1 overflow-y-auto px-3">
          <p className="px-3 pt-1 pb-2 text-xs font-medium tracking-wide uppercase" style={{ color: "var(--muted)" }}>
            المحادثات
          </p>
          {chats.map((c) => (
            <div key={c.id} className="group flex items-center gap-1 rounded-lg my-0.5"
              style={{ background: c.id === activeChatId ? "var(--panel-2)" : "transparent" }}>
              {editingChatId === c.id ? (
                <input autoFocus value={editingChatTitle} onChange={(e) => setEditingChatTitle(e.target.value)}
                  onBlur={() => renameChat(c.id)}
                  onKeyDown={(e) => { if (e.key === "Enter") renameChat(c.id); if (e.key === "Escape") setEditingChatId(null); }}
                  className="flex-1 min-w-0 bg-transparent outline-none px-3 py-2 text-sm" style={{ color: "var(--ink)" }} />
              ) : (
                <button onClick={() => openChat(c.id)} className="flex-1 min-w-0 flex items-center gap-2 text-right px-3 py-2 text-sm"
                  style={{ color: "var(--ink)" }}>
                  <span className="w-1.5 h-1.5 rounded-full shrink-0"
                    style={{ background: c.id === activeChatId ? "var(--crimson)" : "var(--line)" }} aria-hidden="true" />
                  <span className="truncate">{c.title}</span>
                </button>
              )}
              {editingChatId !== c.id && (
                <span className="hidden group-hover:flex items-center gap-0.5 pe-1 shrink-0">
                  <button onClick={() => { setEditingChatId(c.id); setEditingChatTitle(c.title); }} title="إعادة تسمية"
                    className="w-6 h-6 rounded flex items-center justify-center" style={{ color: "var(--muted)" }}><PencilIcon size={13} /></button>
                  <button onClick={() => deleteChat(c.id)} title="حذف"
                    className="w-6 h-6 rounded flex items-center justify-center" style={{ color: "var(--muted)" }}><TrashIcon size={13} /></button>
                </span>
              )}
            </div>
          ))}
          {chats.length === 0 && <p className="px-3 py-2 text-xs" style={{ color: "var(--muted)" }}>لا محادثات بعد</p>}
        </div>
        <div className="p-3" style={{ borderTop: "1px solid var(--line)" }}>
          {user.role === "admin" && (
            <a href="/admin" className="flex items-center gap-2 rounded-xl px-3 py-2 text-sm mb-1"
              style={{ color: "var(--ink)" }}><ChartIcon size={15} />لوحة الأدمن</a>
          )}
          <div className="flex items-center gap-1">
            <a href="/settings" className="flex items-center gap-2 rounded-xl p-2 flex-1 min-w-0"
              style={{ color: "var(--ink)" }} title="الإعدادات">
              <span className="w-8 h-8 rounded-full overflow-hidden flex items-center justify-center shrink-0"
                style={{ background: "var(--panel-2)", border: "1px solid var(--line)" }}>
                {user.avatar
                  ? <img src={user.avatar} alt="" className="w-full h-full object-cover" />
                  : <span className="text-sm font-bold" style={{ color: "var(--muted)" }}>{(user.name || user.email)[0]?.toUpperCase()}</span>}
              </span>
              <span className="flex-1 min-w-0">
                <span className="block truncate text-sm">{user.name || user.email.split("@")[0]}</span>
                <span className="flex items-center gap-1 truncate text-xs" style={{ color: "var(--muted)" }}><GearIcon size={12} />الإعدادات</span>
              </span>
            </a>
            <button onClick={toggleTheme} title="تبديل الثيم" className="w-9 h-9 rounded-xl shrink-0 flex items-center justify-center" style={{ color: "var(--muted)" }}>{dark ? <SunIcon size={17} /> : <MoonIcon size={17} />}</button>
          </div>
        </div>
      </aside>

      {/* Main */}
      <main className="lh-glow flex-1 flex flex-col" style={{ background: "var(--navy)" }}>
        {messages.length === 0 ? (
          <div className="flex-1 flex flex-col items-center justify-center px-4">
            <div className="mb-4">
              <LogoMark size={44} />
            </div>
            <div className="mb-4 flex items-center gap-3">
              <h1 className="lh-greeting text-3xl md:text-4xl" style={{ color: "var(--ink)" }}>{greeting}، {name}</h1>
            </div>
            <div className="w-full max-w-2xl">{composer}</div>
          </div>
        ) : (
          <>
            <div className="flex-1 overflow-y-auto px-4">
              <div className="max-w-3xl mx-auto flex flex-col gap-6 py-6">
                {messages.map((m, i) =>
                  m.role === "user" ? (
                    <div key={i} className="group flex flex-col items-end gap-1">
                      {editingMsgIndex === i ? (
                        <div className="w-full max-w-[80%] flex flex-col gap-2">
                          <textarea autoFocus value={editingMsgContent} onChange={(e) => setEditingMsgContent(e.target.value)}
                            rows={2} className="w-full rounded-2xl px-4 py-2.5 whitespace-pre-wrap leading-relaxed outline-none resize-none"
                            style={{ background: "var(--panel-2)", border: "1px solid var(--crimson)", color: "var(--ink)" }} />
                          <div className="flex gap-2 justify-end">
                            <button onClick={() => setEditingMsgIndex(null)} className="text-xs px-3 py-1 rounded-full"
                              style={{ border: "1px solid var(--line)", color: "var(--muted)" }}>إلغاء</button>
                            <button onClick={() => saveMessageEdit(m.id, i)} className="text-xs px-3 py-1 rounded-full font-semibold text-white"
                              style={{ background: "var(--crimson)" }}>حفظ وإعادة التوليد</button>
                          </div>
                        </div>
                      ) : (
                        <>
                          <div className="rounded-2xl px-4 py-2.5 max-w-[80%] whitespace-pre-wrap leading-relaxed"
                            style={{ background: "var(--panel-2)", border: "1px solid var(--line)" }}>{m.content}</div>
                          <button onClick={() => { setEditingMsgIndex(i); setEditingMsgContent(m.content); }} title="تعديل"
                            className="hidden group-hover:flex items-center gap-1 text-xs px-1" style={{ color: "var(--muted)" }}><PencilIcon size={12} />تعديل</button>
                        </>
                      )}
                    </div>
                  ) : (
                    <div key={i} className="prose-lh w-full min-w-0">
                      {m.content ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown>
                        : <span className="inline-block animate-pulse" style={{ color: "var(--muted)" }}>●●●</span>}
                      {m.attachment && (
                        <a href={`/api/download/${m.attachment.id}`} className="mt-3 flex items-center gap-2 rounded-xl px-3 py-2 font-semibold text-white no-underline w-fit"
                          style={{ background: "var(--crimson)" }}>⬇ تحميل: {m.attachment.name}</a>
                      )}
                    </div>
                  )
                )}
                <div ref={endRef} />
              </div>
            </div>
            <div className="px-4 pb-5"><div className="max-w-3xl mx-auto">{composer}</div></div>
          </>
        )}
      </main>
    </div>
  );
}
