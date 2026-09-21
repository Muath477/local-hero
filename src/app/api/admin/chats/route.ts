import { NextResponse } from "next/server";
import { prisma } from "@/lib/db";
import { getCurrentUser } from "@/lib/auth";
import { can } from "@/lib/permissions";

export const dynamic = "force-dynamic";

// Admin starts a chat owned by someone else — e.g. to seed a conversation
// or investigate on a user's behalf. The chat shows up in *that user's*
// sidebar, not the admin's; ownership is the target user's from creation,
// there's no separate "created by" trail.
export async function POST(req: Request) {
  const me = await getCurrentUser();
  if (!me || !can(me.role, "admin.create_chat_for_user")) {
    return NextResponse.json({ error: "forbidden" }, { status: 403 });
  }

  const { userId, title, model } = await req.json();
  if (!userId) return NextResponse.json({ error: "userId required" }, { status: 400 });

  const target = await prisma.user.findUnique({ where: { id: userId }, select: { id: true } });
  if (!target) return NextResponse.json({ error: "المستخدم غير موجود" }, { status: 404 });

  const chat = await prisma.chat.create({
    data: {
      userId,
      title: title?.trim() || "محادثة جديدة (من الإدارة)",
      model: model || "general-assistant-plus",
    },
  });
  return NextResponse.json({ chat });
}
