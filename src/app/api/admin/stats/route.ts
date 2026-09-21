import { NextResponse } from "next/server";
import { prisma } from "@/lib/db";
import { getCurrentUser } from "@/lib/auth";
import { can } from "@/lib/permissions";

// Word-frequency "interests" (the old approach here) moved out entirely —
// see src/lib/topics.ts + /api/admin/topics for the embedding-based
// replacement. This file now only owns headcounts, the users table, and
// time-range stats.

const RANGE_SPAN_MS: Record<string, number | null> = {
  today: 24 * 60 * 60 * 1000,
  "7d": 7 * 24 * 60 * 60 * 1000,
  "30d": 30 * 24 * 60 * 60 * 1000,
  all: null,
};

type Window = { current: { gte: Date } | undefined; previous: { gte: Date; lt: Date } | null };

function rangeWindows(range: string): Window {
  const span = RANGE_SPAN_MS[range] ?? null;
  if (span === null) return { current: undefined, previous: null };
  const now = Date.now();
  return {
    current: { gte: new Date(now - span) },
    previous: { gte: new Date(now - 2 * span), lt: new Date(now - span) },
  };
}

// {value, delta}: delta is null for "all" (no meaningful previous period
// to compare a running total against) or when the two counts can't be
// compared yet.
type Stat = { value: number; delta: number | null };

function toStat(current: number, previous: number | null): Stat {
  return { value: current, delta: previous === null ? null : current - previous };
}

export async function GET(req: Request) {
  const me = await getCurrentUser();
  if (!me || !can(me.role, "admin.view_dashboard")) {
    return NextResponse.json({ error: "forbidden" }, { status: 403 });
  }

  const params = new URL(req.url).searchParams;
  const includeAdmins = params.get("includeAdmins") === "true";
  const range = params.get("range") || "all";
  const { current, previous } = rangeWindows(range);

  const roleFilter = includeAdmins ? {} : { role: "user" as const };
  const chatUserFilter = includeAdmins ? {} : { user: { role: "user" as const } };
  const messageChatUserFilter = includeAdmins ? {} : { chat: { user: { role: "user" as const } } };

  const [userCountCur, chatCountCur, messageCountCur] = await Promise.all([
    prisma.user.count({ where: { ...roleFilter, createdAt: current } }),
    prisma.chat.count({ where: { ...chatUserFilter, createdAt: current } }),
    prisma.message.count({ where: { role: "user", ...messageChatUserFilter, createdAt: current } }),
  ]);

  let userCountPrev: number | null = null;
  let chatCountPrev: number | null = null;
  let messageCountPrev: number | null = null;
  if (previous) {
    [userCountPrev, chatCountPrev, messageCountPrev] = await Promise.all([
      prisma.user.count({ where: { ...roleFilter, createdAt: previous } }),
      prisma.chat.count({ where: { ...chatUserFilter, createdAt: previous } }),
      prisma.message.count({ where: { role: "user", ...messageChatUserFilter, createdAt: previous } }),
    ]);
  }

  // Online-now is a live snapshot, not a period total — showing "online in
  // the last 30 days" would be a different (and less useful) metric, so
  // this one deliberately ignores `range` entirely.
  const ONLINE_WINDOW_MS = 90_000;
  const now = Date.now();

  const rawUsers = await prisma.user.findMany({
    where: roleFilter,
    orderBy: { createdAt: "desc" },
    select: {
      id: true,
      email: true,
      name: true,
      role: true,
      disabled: true,
      createdAt: true,
      lastSeenAt: true,
      chats: { select: { updatedAt: true, _count: { select: { messages: true } } } },
    },
  });

  const users = rawUsers.map((u) => ({
    id: u.id,
    email: u.email,
    name: u.name,
    role: u.role,
    disabled: u.disabled,
    createdAt: u.createdAt,
    chatCount: u.chats.length,
    messageCount: u.chats.reduce((sum, c) => sum + c._count.messages, 0),
    lastActive: u.chats.reduce<Date | null>(
      (latest, c) => (!latest || c.updatedAt > latest ? c.updatedAt : latest),
      null,
    ),
    lastSeenAt: u.lastSeenAt,
    online: u.lastSeenAt != null && now - u.lastSeenAt.getTime() < ONLINE_WINDOW_MS,
  }));

  return NextResponse.json({
    range,
    overview: {
      userCount: toStat(userCountCur, userCountPrev),
      onlineCount: users.filter((u) => u.online).length,
      chatCount: toStat(chatCountCur, chatCountPrev),
      messageCount: toStat(messageCountCur, messageCountPrev),
    },
    users,
  });
}
