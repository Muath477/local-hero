import { NextResponse } from "next/server";
import { cookies } from "next/headers";
import { prisma } from "@/lib/db";
import { createSession } from "@/lib/auth";

export const dynamic = "force-dynamic";

type GoogleUserInfo = {
  sub: string;
  email: string;
  email_verified: boolean;
  name?: string;
  picture?: string;
};

export async function GET(req: Request) {
  const url = new URL(req.url);
  const code = url.searchParams.get("code");
  const state = url.searchParams.get("state");
  const expectedState = (await cookies()).get("lh_oauth_state")?.value;

  if (!code || !state || state !== expectedState) {
    return NextResponse.redirect(new URL("/login?error=google_state", url.origin));
  }

  const clientId = process.env.GOOGLE_CLIENT_ID;
  const clientSecret = process.env.GOOGLE_CLIENT_SECRET;
  const redirectUri = process.env.GOOGLE_REDIRECT_URI || "http://localhost:3000/api/auth/google/callback";
  if (!clientId || !clientSecret) {
    return NextResponse.redirect(new URL("/login?error=google_not_configured", url.origin));
  }

  // Exchange the authorization code for an access token.
  const tokenRes = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      code,
      client_id: clientId,
      client_secret: clientSecret,
      redirect_uri: redirectUri,
      grant_type: "authorization_code",
    }),
  });
  if (!tokenRes.ok) {
    return NextResponse.redirect(new URL("/login?error=google_token", url.origin));
  }
  const { access_token } = await tokenRes.json();

  const userRes = await fetch("https://www.googleapis.com/oauth2/v3/userinfo", {
    headers: { Authorization: `Bearer ${access_token}` },
  });
  if (!userRes.ok) {
    return NextResponse.redirect(new URL("/login?error=google_userinfo", url.origin));
  }
  const profile: GoogleUserInfo = await userRes.json();

  // Match an existing account by googleId first, then by email (lets
  // someone who registered with a password link their Google account by
  // signing in with the same address), otherwise create a new one.
  let user = await prisma.user.findFirst({ where: { OR: [{ googleId: profile.sub }, { email: profile.email }] } });
  if (!user) {
    const isFirst = (await prisma.user.count()) === 0;
    user = await prisma.user.create({
      data: {
        email: profile.email,
        name: profile.name || null,
        avatar: profile.picture || null,
        googleId: profile.sub,
        passwordHash: null,
        role: isFirst ? "admin" : "user",
      },
    });
  } else if (!user.googleId) {
    user = await prisma.user.update({ where: { id: user.id }, data: { googleId: profile.sub } });
  }

  if (user.disabled) {
    return NextResponse.redirect(new URL("/login?error=account_disabled", url.origin));
  }

  await createSession(user.id);
  const res = NextResponse.redirect(new URL("/", url.origin));
  res.cookies.delete("lh_oauth_state");
  return res;
}
