import { NextResponse } from "next/server";
import { prisma } from "@/lib/db";
import { hashPassword, createSession } from "@/lib/auth";

export async function POST(req: Request) {
  const { email, password, name } = await req.json();
  if (!email || !password) {
    return NextResponse.json({ error: "البريد وكلمة المرور مطلوبة" }, { status: 400 });
  }
  const existing = await prisma.user.findUnique({ where: { email } });
  if (existing) {
    return NextResponse.json({ error: "هذا البريد مسجّل مسبقًا" }, { status: 409 });
  }
  // The very first account becomes the admin.
  const isFirst = (await prisma.user.count()) === 0;
  const user = await prisma.user.create({
    data: {
      email,
      name: name || null,
      passwordHash: await hashPassword(password),
      role: isFirst ? "admin" : "user",
    },
  });
  await createSession(user.id);
  return NextResponse.json({ id: user.id, email: user.email, role: user.role });
}
