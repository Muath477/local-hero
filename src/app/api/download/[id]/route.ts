import { readFile } from "fs/promises";
import { prisma } from "@/lib/db";
import { getSessionUserId } from "@/lib/auth";

const DOCX_MIME =
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const userId = await getSessionUserId();
  if (!userId) return new Response("unauthorized", { status: 401 });
  const { id } = await params;

  const attachment = await prisma.attachment.findFirst({ where: { id, userId } });
  if (!attachment) return new Response("not found", { status: 404 });

  let data: Buffer;
  try {
    data = await readFile(attachment.path);
  } catch {
    return new Response("file missing", { status: 410 });
  }

  return new Response(new Uint8Array(data), {
    headers: {
      "Content-Type": DOCX_MIME,
      "Content-Disposition": `attachment; filename="download.docx"; filename*=UTF-8''${encodeURIComponent(attachment.name)}`,
    },
  });
}
