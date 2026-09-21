import { NextResponse } from "next/server";
import { prisma } from "@/lib/db";
import { getCurrentUser } from "@/lib/auth";
import { can } from "@/lib/permissions";
import { classifyMessages, aggregateCounts, OTHER_CATEGORY } from "@/lib/topics";

export const dynamic = "force-dynamic";

// Deliberately its own endpoint, not folded into /api/admin/stats: stats
// refreshes every 20s for live online-status (see AdminDashboard.tsx), and
// re-embedding every user message that often would hammer Ollama for no
// benefit — topic distribution doesn't change meaningfully second to
// second. The dashboard fetches this once on load plus a manual refresh.
//
// One embedding pass classifies every message; global counts and each
// user's top topics are both aggregated from those same labels, so the
// per-user breakdown in the users table doesn't cost a second round of
// Ollama calls.
export async function GET(req: Request) {
  const me = await getCurrentUser();
  if (!me || !can(me.role, "admin.view_dashboard")) {
    return NextResponse.json({ error: "forbidden" }, { status: 403 });
  }

  const includeAdmins = new URL(req.url).searchParams.get("includeAdmins") === "true";

  const userMessages = await prisma.message.findMany({
    where: {
      role: "user",
      ...(includeAdmins ? {} : { chat: { user: { role: "user" } } }),
    },
    select: { content: true, chat: { select: { userId: true } } },
  });

  try {
    const labels = await classifyMessages(userMessages.map((m) => m.content));
    const topics = aggregateCounts(labels);

    const byUser = new Map<string, Map<string, number>>();
    userMessages.forEach((m, i) => {
      const label = labels[i];
      if (label === OTHER_CATEGORY) return; // not useful to show per user
      const uid = m.chat.userId;
      if (!byUser.has(uid)) byUser.set(uid, new Map());
      const userCounts = byUser.get(uid)!;
      userCounts.set(label, (userCounts.get(label) ?? 0) + 1);
    });

    const byUserTop = Object.fromEntries(
      [...byUser.entries()].map(([uid, counts]) => [
        uid,
        [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([category]) => category),
      ]),
    );

    return NextResponse.json({ topics, byUser: byUserTop, sampleSize: userMessages.length });
  } catch {
    return NextResponse.json(
      { error: "تعذّر تصنيف المواضيع — تأكد أن Ollama يعمل على المنفذ 11434" },
      { status: 502 },
    );
  }
}
