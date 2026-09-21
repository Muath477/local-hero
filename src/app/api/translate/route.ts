import { NextResponse } from "next/server";
import { writeFile, mkdir } from "fs/promises";
import { randomUUID } from "crypto";
import path from "path";
import { prisma } from "@/lib/db";
import { getSessionUserId } from "@/lib/auth";

export const dynamic = "force-dynamic";

const AGENTS_URL = process.env.AGENTS_URL || "http://localhost:9099";
const LANG_LABEL: Record<string, string> = { Arabic: "العربية", English: "الإنجليزية" };

// Tarjuman in-chat: receive a .docx + target language, translate it via the
// agents service (format preserved), store the result, and record it in chat.
export async function POST(req: Request) {
  const userId = await getSessionUserId();
  if (!userId) return NextResponse.json({ error: "unauthorized" }, { status: 401 });

  const form = await req.formData();
  const file = form.get("file") as File | null;
  const targetLanguage = (form.get("targetLanguage") as string) || "Arabic";
  const chatId = (form.get("chatId") as string) || null;

  if (!file) return NextResponse.json({ error: "لم يُرفق ملف" }, { status: 400 });
  if (!file.name.toLowerCase().endsWith(".docx")) {
    return NextResponse.json({ error: "الملف يجب أن يكون ‎.docx‎" }, { status: 415 });
  }

  // Forward to the Tarjuman endpoint.
  const forward = new FormData();
  forward.append("file", file, file.name);
  forward.append("target_language", targetLanguage);

  let res: Response;
  try {
    res = await fetch(`${AGENTS_URL}/tarjuman/translate`, { method: "POST", body: forward });
  } catch {
    return NextResponse.json({ error: "تعذّر الاتصال بخدمة الترجمة (المنفذ 9099)" }, { status: 502 });
  }
  if (!res.ok) {
    let reason = "فشلت الترجمة";
    try {
      reason = (await res.json()).error?.message || reason;
    } catch {}
    return NextResponse.json({ error: reason }, { status: 502 });
  }

  // Store the translated file.
  const buffer = Buffer.from(await res.arrayBuffer());
  const stem = file.name.replace(/\.docx$/i, "");
  const outName = targetLanguage === "Arabic" ? `${stem}.ar.docx` : `${stem}.translated.docx`;
  const storageDir = path.join(process.cwd(), "storage");
  await mkdir(storageDir, { recursive: true });
  const storedPath = path.join(storageDir, `${randomUUID()}.docx`);
  await writeFile(storedPath, buffer);

  const attachment = await prisma.attachment.create({
    data: { userId, name: outName, path: storedPath },
  });

  // Record it as a chat exchange.
  let chat = chatId ? await prisma.chat.findFirst({ where: { id: chatId, userId } }) : null;
  if (!chat) {
    chat = await prisma.chat.create({
      data: { userId, model: "tarjuman", title: `ترجمة: ${file.name}` },
    });
  }
  await prisma.message.create({
    data: {
      chatId: chat.id,
      role: "user",
      content: `ترجمة الملف "${file.name}" إلى ${LANG_LABEL[targetLanguage] || targetLanguage}`,
    },
  });
  await prisma.message.create({
    data: {
      chatId: chat.id,
      role: "assistant",
      content: "تمت الترجمة بنجاح مع الحفاظ على التنسيق ✓",
      attachmentId: attachment.id,
    },
  });

  return NextResponse.json({
    chatId: chat.id,
    attachment: { id: attachment.id, name: attachment.name },
  });
}
