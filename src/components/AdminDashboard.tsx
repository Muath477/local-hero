"use client";

import { useEffect, useState } from "react";
import LogoMark from "@/components/LogoMark";
import { RefreshIcon } from "@/components/Icons";

type Stat = { value: number; delta: number | null };
type Overview = { userCount: Stat; onlineCount: number; chatCount: Stat; messageCount: Stat };
type UserRow = {
  id: string;
  email: string;
  name: string | null;
  role: string;
  disabled: boolean;
  createdAt: string;
  chatCount: number;
  messageCount: number;
  lastActive: string | null;
  lastSeenAt: string | null;
  online: boolean;
};
type StatsResponse = { range: string; overview: Overview; users: UserRow[] };
type Topic = { category: string; count: number };
type TopicsResponse = { topics: Topic[]; byUser: Record<string, string[]>; sampleSize: number };

const RANGES: { id: string; label: string }[] = [
  { id: "today", label: "اليوم" },
  { id: "7d", label: "7 أيام" },
  { id: "30d", label: "30 يوم" },
  { id: "all", label: "كل الوقت" },
];

function fmtDate(d: string | null) {
  if (!d) return "—";
  return new Date(d).toLocaleDateString("ar", { year: "numeric", month: "short", day: "numeric" });
}

function fmtRelative(d: string | null) {
  if (!d) return "لم يتصل بعد";
  const seconds = Math.floor((Date.now() - new Date(d).getTime()) / 1000);
  if (seconds < 60) return "قبل لحظات";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `قبل ${minutes} د`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `قبل ${hours} س`;
  const days = Math.floor(hours / 24);
  return `قبل ${days} يوم`;
}

export default function AdminDashboard() {
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [topicsData, setTopicsData] = useState<TopicsResponse | null>(null);
  const [topicsLoading, setTopicsLoading] = useState(false);
  const [topicsError, setTopicsError] = useState("");
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [range, setRange] = useState("all");
  const [includeAdmins, setIncludeAdmins] = useState(false);

  const loadStats = () =>
    fetch(`/api/admin/stats?range=${range}&includeAdmins=${includeAdmins}`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then(setStats)
      .catch(() => setError("تعذّر تحميل الإحصاءات"));

  function loadTopics() {
    setTopicsLoading(true);
    setTopicsError("");
    return fetch(`/api/admin/topics?includeAdmins=${includeAdmins}`)
      .then(async (r) => {
        const d = await r.json();
        if (!r.ok) throw new Error(d.error || "تعذّر تحميل المواضيع");
        setTopicsData(d);
      })
      .catch((e: Error) => setTopicsError(e.message))
      .finally(() => setTopicsLoading(false));
  }

  useEffect(() => {
    loadStats();
    // Online status is live, so a one-time load would go stale the moment
    // someone closes a tab — refresh while this page itself stays open.
    // Topics are NOT on this interval — see loadTopics()'s own effect and
    // the comment in /api/admin/topics/route.ts for why.
    const id = setInterval(loadStats, 20_000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range, includeAdmins]);

  useEffect(() => {
    loadTopics();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [includeAdmins]);

  async function toggleUser(id: string, disabled: boolean) {
    setBusyId(id);
    try {
      await fetch(`/api/admin/users/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ disabled }),
      });
      await loadStats();
    } finally { setBusyId(null); }
  }

  async function createChatFor(id: string) {
    setBusyId(id);
    try {
      await fetch("/api/admin/chats", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ userId: id }),
      });
      await loadStats();
    } finally { setBusyId(null); }
  }

  const card = (label: string, stat: Stat | number) => {
    const value = typeof stat === "number" ? stat : stat.value;
    const delta = typeof stat === "number" ? null : stat.delta;
    return (
      <div className="rounded-2xl p-5" style={{ background: "var(--panel)", border: "1px solid var(--line)" }}>
        <div className="text-3xl font-extrabold">{value}</div>
        <div className="text-sm mt-1 flex items-center gap-2 flex-wrap" style={{ color: "var(--muted)" }}>
          <span>{label}</span>
          {delta !== null && delta !== 0 && (
            <span className="text-xs font-semibold" style={{ color: delta > 0 ? "#4ade80" : "#e0503b" }}>
              {delta > 0 ? `+${delta}` : delta} مقابل الفترة السابقة
            </span>
          )}
        </div>
      </div>
    );
  };

  const maxTopic = topicsData?.topics[0]?.count || 1;
  // Online users pinned to the top regardless of the table's own createdAt
  // ordering from the API — a currently-connected user is the one an admin
  // most likely wants to see first.
  const sortedUsers = stats ? [...stats.users].sort((a, b) => (b.online ? 1 : 0) - (a.online ? 1 : 0)) : [];

  return (
    <div className="min-h-screen">
      <header className="flex items-center gap-3 p-4" style={{ borderBottom: "1px solid var(--line)" }}>
        <LogoMark size={34} />
        <span className="font-extrabold text-lg">لوحة الأدمن</span>
        <a href="/" className="ms-auto text-sm rounded-lg px-3 py-1.5"
          style={{ background: "var(--panel)", border: "1px solid var(--line)", color: "var(--ink)" }}>
          ← رجوع للمحادثة
        </a>
      </header>

      <div className="max-w-5xl mx-auto p-6 flex flex-col gap-6">
        {error && <p style={{ color: "#ff8b93" }}>{error}</p>}
        {!stats && !error && <p style={{ color: "var(--muted)" }}>جارٍ التحميل…</p>}

        {stats && (
          <>
            <div className="flex items-center justify-between flex-wrap gap-3">
              <div className="flex gap-1 p-1 rounded-xl" style={{ background: "var(--navy)" }}>
                {RANGES.map((r) => (
                  <button key={r.id} onClick={() => setRange(r.id)}
                    className="px-3 py-1.5 rounded-lg text-sm font-medium"
                    style={range === r.id ? { background: "var(--crimson)", color: "#fff" } : { color: "var(--muted)" }}>
                    {r.label}
                  </button>
                ))}
              </div>
              <label className="flex items-center gap-2 text-sm cursor-pointer" style={{ color: "var(--muted)" }}>
                <input type="checkbox" checked={includeAdmins} onChange={(e) => setIncludeAdmins(e.target.checked)} />
                عرض حسابات الأدمن
              </label>
            </div>

            <div className="grid grid-cols-4 gap-4">
              {card("المستخدمون", stats.overview.userCount)}
              {card("متصلون الآن", stats.overview.onlineCount)}
              {card("المحادثات", stats.overview.chatCount)}
              {card("الرسائل", stats.overview.messageCount)}
            </div>

            <section className="rounded-2xl p-5" style={{ background: "var(--panel)", border: "1px solid var(--line)" }}>
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-lg font-bold">مواضيع المحادثات (تصنيف دلالي بالتضمينات)</h2>
                <button onClick={loadTopics} disabled={topicsLoading} className="flex items-center gap-1.5 text-xs px-2 py-1 rounded-lg disabled:opacity-40"
                  style={{ border: "1px solid var(--line)", color: "var(--ink)" }}>
                  {topicsLoading ? "…" : <><RefreshIcon size={13} />تحديث</>}
                </button>
              </div>
              {topicsError && <p className="text-sm" style={{ color: "#ff8b93" }}>{topicsError}</p>}
              {!topicsError && topicsLoading && !topicsData && <p style={{ color: "var(--muted)" }}>جارٍ التصنيف…</p>}
              {!topicsError && topicsData && topicsData.topics.length === 0 && (
                <p style={{ color: "var(--muted)" }}>لا توجد بيانات كافية بعد.</p>
              )}
              {!topicsError && topicsData && topicsData.topics.length > 0 && (
                <div className="flex flex-col gap-2">
                  {topicsData.topics.map((t) => (
                    <div key={t.category} className="flex items-center gap-3">
                      <span className="w-40 shrink-0 truncate text-sm" title={t.category}>{t.category}</span>
                      <div className="flex-1 rounded-full h-3" style={{ background: "var(--navy)" }}>
                        <div className="h-3 rounded-full" style={{ width: `${(t.count / maxTopic) * 100}%`, background: "var(--crimson)" }} />
                      </div>
                      <span className="w-8 text-sm text-left" style={{ color: "var(--muted)" }}>{t.count}</span>
                    </div>
                  ))}
                </div>
              )}
            </section>

            <section className="rounded-2xl p-5 overflow-x-auto" style={{ background: "var(--panel)", border: "1px solid var(--line)" }}>
              <h2 className="text-lg font-bold mb-4">المستخدمون</h2>
              <table className="w-full text-sm" style={{ tableLayout: "fixed" }}>
                <colgroup>
                  <col style={{ width: "20%" }} />
                  <col style={{ width: "11%" }} />
                  <col style={{ width: "7%" }} />
                  <col style={{ width: "8%" }} />
                  <col style={{ width: "8%" }} />
                  <col style={{ width: "18%" }} />
                  <col style={{ width: "9%" }} />
                  <col style={{ width: "9%" }} />
                  <col style={{ width: "10%" }} />
                </colgroup>
                <thead>
                  <tr style={{ color: "var(--muted)" }} className="text-right">
                    <th className="py-2 font-medium">البريد</th>
                    <th className="py-2 font-medium">الحالة</th>
                    <th className="py-2 font-medium">الدور</th>
                    <th className="py-2 font-medium">المحادثات</th>
                    <th className="py-2 font-medium">الرسائل</th>
                    <th className="py-2 font-medium">الاهتمامات</th>
                    <th className="py-2 font-medium">انضم</th>
                    <th className="py-2 font-medium">آخر نشاط</th>
                    <th className="py-2 font-medium">إجراءات</th>
                  </tr>
                </thead>
                <tbody>
                  {sortedUsers.map((u) => {
                    const userTopics = topicsData?.byUser[u.id] ?? [];
                    const shown = userTopics.slice(0, 2);
                    const extra = userTopics.length - shown.length;
                    return (
                      <tr key={u.id} style={{
                        borderTop: "1px solid var(--line)",
                        opacity: u.disabled ? 0.55 : 1,
                        background: u.online ? "rgba(74, 222, 128, 0.08)" : "transparent",
                      }}>
                        <td className="py-2 truncate">
                          {u.email}
                          {u.disabled && (
                            <span className="ms-2 text-xs font-bold px-2 py-0.5 rounded" style={{ background: "var(--muted)", color: "var(--panel)" }}>
                              معطَّل
                            </span>
                          )}
                        </td>
                        <td className="py-2">
                          {u.online ? (
                            <span className="inline-flex items-center gap-1.5 text-xs font-medium" style={{ color: "#4ade80" }}>
                              <span className="w-2 h-2 rounded-full shrink-0" style={{ background: "#4ade80" }} />
                              متصل الآن
                            </span>
                          ) : (
                            <span className="inline-flex items-center gap-1.5 text-xs" style={{ color: "var(--muted)" }}>
                              <span className="w-2 h-2 rounded-full shrink-0" style={{ background: "var(--line)" }} />
                              {fmtRelative(u.lastSeenAt)}
                            </span>
                          )}
                        </td>
                        <td className="py-2">
                          {u.role === "admin"
                            ? <span className="text-xs font-bold px-2 py-0.5 rounded" style={{ background: "var(--crimson)", color: "#fff" }}>admin</span>
                            : <span style={{ color: "var(--muted)" }}>user</span>}
                        </td>
                        <td className="py-2">{u.chatCount}</td>
                        <td className="py-2">{u.messageCount}</td>
                        <td className="py-2">
                          <div className="flex gap-1 flex-wrap items-center">
                            {shown.length === 0 && <span style={{ color: "var(--muted)" }}>—</span>}
                            {shown.map((w) => (
                              <span key={w} className="text-xs px-2 py-0.5 rounded-full truncate max-w-[6.5rem]"
                                style={{ background: "var(--navy)", border: "1px solid var(--line)" }} title={w}>{w}</span>
                            ))}
                            {extra > 0 && <span className="text-xs shrink-0" style={{ color: "var(--muted)" }}>+{extra}</span>}
                          </div>
                        </td>
                        <td className="py-2" style={{ color: "var(--muted)" }}>{fmtDate(u.createdAt)}</td>
                        <td className="py-2" style={{ color: "var(--muted)" }}>{fmtDate(u.lastActive)}</td>
                        <td className="py-2">
                          <div className="flex gap-1.5">
                            <button onClick={() => createChatFor(u.id)} disabled={busyId === u.id || u.disabled}
                              title="إنشاء محادثة لهذا المستخدم"
                              className="text-xs px-2 py-1 rounded-lg disabled:opacity-40"
                              style={{ border: "1px solid var(--line)", color: "var(--ink)" }}>
                              + محادثة
                            </button>
                            {u.role !== "admin" && (
                              <button onClick={() => toggleUser(u.id, !u.disabled)} disabled={busyId === u.id}
                                className="text-xs px-2 py-1 rounded-lg disabled:opacity-40"
                                style={u.disabled
                                  ? { border: "1px solid var(--line)", color: "var(--ink)" }
                                  : { border: "1px solid transparent", background: "#e0503b", color: "#fff" }}>
                                {u.disabled ? "تفعيل" : "تعطيل"}
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </section>
          </>
        )}
      </div>
    </div>
  );
}
