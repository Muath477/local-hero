import { NextResponse } from "next/server";
import { prisma } from "@/lib/db";
import { getCurrentUser } from "@/lib/auth";
import { can } from "@/lib/permissions";

export const dynamic = "force-dynamic";

// Suspend or reactivate a user. A disabled account is blocked immediately —
// see getSessionUserId() in lib/auth.ts, which checks this flag on every
// request, not just at login.
export async function PATCH(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const me = await getCurrentUser();
  if (!me || !can(me.role, "admin.toggle_user")) {
    return NextResponse.json({ error: "forbidden" }, { status: 403 });
  }

  const { id } = await params;
  if (id === me.id) {
    return NextResponse.json({ error: "لا يمكنك تعطيل حسابك الخاص" }, { status: 400 });
  }

  const { disabled } = await req.json();
  if (typeof disabled !== "boolean") {
    return NextResponse.json({ error: "disabled must be boolean" }, { status: 400 });
  }

  const result = await prisma.user.updateMany({ where: { id }, data: { disabled } });
  if (result.count === 0) return NextResponse.json({ error: "not found" }, { status: 404 });
  return NextResponse.json({ ok: true, disabled });
}
