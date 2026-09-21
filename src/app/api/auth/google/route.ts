import { NextResponse } from "next/server";
import { randomUUID } from "crypto";

// Kicks off "Sign in with Google": stash a random state value in a
// short-lived cookie (checked back against Google's redirect in
// callback/route.ts to rule out CSRF), then bounce the browser to
// Google's consent screen.
export async function GET() {
  const clientId = process.env.GOOGLE_CLIENT_ID;
  const redirectUri = process.env.GOOGLE_REDIRECT_URI || "http://localhost:3000/api/auth/google/callback";
  if (!clientId) {
    return NextResponse.json({ error: "GOOGLE_CLIENT_ID غير مضبوط بملف .env" }, { status: 500 });
  }

  const state = randomUUID();
  const url = new URL("https://accounts.google.com/o/oauth2/v2/auth");
  url.searchParams.set("client_id", clientId);
  url.searchParams.set("redirect_uri", redirectUri);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("scope", "openid email profile");
  url.searchParams.set("state", state);
  url.searchParams.set("prompt", "select_account");

  const res = NextResponse.redirect(url.toString());
  res.cookies.set("lh_oauth_state", state, { httpOnly: true, sameSite: "lax", path: "/", maxAge: 600 });
  return res;
}
