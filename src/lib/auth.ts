import bcrypt from "bcryptjs";
import { SignJWT, jwtVerify } from "jose";
import { cookies } from "next/headers";
import { prisma } from "./db";

const secret = new TextEncoder().encode(process.env.AUTH_SECRET || "dev-secret");
const COOKIE = "lh_session";
const MAX_AGE = 60 * 60 * 24 * 30; // 30 days

export function hashPassword(password: string) {
  return bcrypt.hash(password, 10);
}

export function verifyPassword(password: string, hash: string) {
  return bcrypt.compare(password, hash);
}

export async function createSession(userId: string) {
  const token = await new SignJWT({ sub: userId })
    .setProtectedHeader({ alg: "HS256" })
    .setExpirationTime("30d")
    .sign(secret);
  (await cookies()).set(COOKIE, token, {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    maxAge: MAX_AGE,
  });
}

export async function clearSession() {
  (await cookies()).delete(COOKIE);
}

export async function getSessionUserId(): Promise<string | null> {
  const token = (await cookies()).get(COOKIE)?.value;
  if (!token) return null;
  let id: string | null;
  try {
    const { payload } = await jwtVerify(token, secret);
    id = (payload.sub as string) ?? null;
  } catch {
    return null;
  }
  if (!id) return null;

  // Checked here, not just at login: this is the one function nearly every
  // route (chat, upload, translate, heartbeat, chats...) calls to identify
  // the caller, so an admin disabling someone takes effect on their very
  // next request — not just the next time they try to log in with an
  // already-valid 30-day session cookie.
  const user = await prisma.user.findUnique({ where: { id }, select: { disabled: true } });
  if (!user || user.disabled) return null;
  return id;
}

export async function getCurrentUser() {
  const id = await getSessionUserId();
  if (!id) return null;
  return prisma.user.findUnique({
    where: { id },
    select: { id: true, email: true, name: true, role: true, avatar: true },
  });
}
