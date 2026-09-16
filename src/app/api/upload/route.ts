import { NextResponse } from "next/server";
import { getSessionUserId } from "@/lib/auth";

const AGENTS_URL = process.env.AGENTS_URL || "http://localhost:9099";

export const dynamic = "force-dynamic";

// Any file the "file-analyst" agent should be able to answer questions
// about — not just .docx (that's Tarjuman's separate translate-only path
// in /api/translate). Forwards straight to the agents service's own
// /upload, which does the chunk+embed+index work.
export async function POST(req: Request) {
  const userId = await getSessionUserId();
  if (!userId) return NextResponse.json({ error: "unauthorized" }, { status: 401 });

  const form = await req.formData();
  const file = form.get("file") as File | null;
  if (!file) return NextResponse.json({ error: "لم يُرفق ملف" }, { status: 400 });

  const forward = new FormData();
  forward.append("file", file, file.name);

  let res: Response;
  try {
    res = await fetch(`${AGENTS_URL}/upload`, { method: "POST", body: forward });
  } catch {
    return NextResponse.json({ error: "تعذّر الاتصال بخدمة الملفات (المنفذ 9099)" }, { status: 502 });
  }

  const data = await res.json();
  if (!res.ok) {
    return NextResponse.json({ error: data.detail || "فشل رفع الملف" }, { status: res.status });
  }
  return NextResponse.json(data);
}
