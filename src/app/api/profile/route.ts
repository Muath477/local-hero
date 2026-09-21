import { NextResponse } from "next/server";
import { prisma } from "@/lib/db";
import { getSessionUserId } from "@/lib/auth";

export const dynamic = "force-dynamic";

export async function PATCH(req: Request) {
  const userId = await getSessionUserId();
  if (!userId) return NextResponse.json({ error: "unauthorized" }, { status: 401 });

  const { name, avatar } = await req.json();
  const data: Record<string, string | null> = {};

  if (typeof name === "string") data.name = name.trim() || null;
  if (typeof avatar === "string") {
    if (avatar.length > 700_000) {
      return NextResponse.json({ error: "الصورة كبيرة جدًا (الحد ~500KB)" }, { status: 413 });
    }
    data.avatar = avatar;
  }

  const user = await prisma.user.update({
    where: { id: userId },
    data,
    select: { id: true, email: true, name: true, role: true, avatar: true },
  });
  return NextResponse.json({ user });
}
