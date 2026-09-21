import { NextResponse } from "next/server";
import { prisma } from "@/lib/db";
import { getSessionUserId } from "@/lib/auth";

export const dynamic = "force-dynamic";

// Called on page load and every 30s while a tab is open (see Chat.tsx).
// The admin dashboard treats anyone touched in the last ~90s as "online" —
// a heartbeat gap that size safely survives one missed tick from a
// backgrounded tab without flapping the status on every poll.
export async function POST() {
  const userId = await getSessionUserId();
  if (!userId) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  await prisma.user.update({ where: { id: userId }, data: { lastSeenAt: new Date() } });
  return NextResponse.json({ ok: true });
}
