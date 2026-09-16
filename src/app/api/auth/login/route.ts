import { NextResponse } from "next/server";
import { prisma } from "@/lib/db";
import { verifyPassword, createSession } from "@/lib/auth";

export async function POST(req: Request) {
  const { email, password } = await req.json();
  const user = await prisma.user.findUnique({ where: { email } });
  if (!user || !user.passwordHash) {
    return NextResponse.json(
      { error: !user ? "بيانات الدخول غير صحيحة" : "هذا الحساب مسجَّل عبر قوقل — استخدم زر الدخول بقوقل" },
      { status: 401 },
    );
  }
  if (!(await verifyPassword(password, user.passwordHash))) {
    return NextResponse.json({ error: "بيانات الدخول غير صحيحة" }, { status: 401 });
  }
  if (user.disabled) {
    return NextResponse.json({ error: "هذا الحساب معطَّل. تواصل مع الإدارة." }, { status: 403 });
  }
  await createSession(user.id);
  return NextResponse.json({ id: user.id, email: user.email, role: user.role });
}
