import { prisma } from "@/lib/db";
import { getSessionUserId } from "@/lib/auth";
import { runAssistantReply } from "@/lib/runReply";

export const dynamic = "force-dynamic";

// Edit a user message. ChatGPT-style semantics: the old answer no longer
// matches the edited question, so everything after this message (the
// assistant's reply, and anything after that) is discarded, and a fresh
// reply is streamed back — same as sending a new message, except the
// history it's answering from now has the corrected text in place.
export async function PATCH(req: Request, { params }: { params: Promise<{ id: string; messageId: string }> }) {
  const userId = await getSessionUserId();
  if (!userId) return new Response("unauthorized", { status: 401 });

  const { id, messageId } = await params;
  const { content } = await req.json();
  if (!content?.trim()) return new Response("empty message", { status: 400 });

  const chat = await prisma.chat.findFirst({ where: { id, userId } });
  if (!chat) return new Response("not found", { status: 404 });

  const message = await prisma.message.findFirst({ where: { id: messageId, chatId: id } });
  if (!message || message.role !== "user") {
    return new Response("only user messages can be edited", { status: 400 });
  }

  await prisma.message.update({ where: { id: messageId }, data: { content: content.trim() } });
  await prisma.message.deleteMany({ where: { chatId: id, createdAt: { gt: message.createdAt } } });

  return runAssistantReply(id, chat.model);
}
