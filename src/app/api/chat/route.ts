import { prisma } from "@/lib/db";
import { getSessionUserId } from "@/lib/auth";
import { runAssistantReply } from "@/lib/runReply";
import { truncateText } from "@/lib/text";

export const dynamic = "force-dynamic";

// Send a message: persist it, then hand off to runAssistantReply for the
// model call + streaming + persisting the answer.
export async function POST(req: Request) {
  const userId = await getSessionUserId();
  if (!userId) return new Response("unauthorized", { status: 401 });

  const { chatId, model, content } = await req.json();
  if (!content?.trim()) return new Response("empty message", { status: 400 });

  let chat = chatId ? await prisma.chat.findFirst({ where: { id: chatId, userId } }) : null;
  if (!chat) {
    chat = await prisma.chat.create({
      data: { userId, model: model || "general-assistant-plus", title: truncateText(content, 40) },
    });
  } else if (model && model !== chat.model) {
    // The model switcher only matters if changing it mid-chat actually
    // sticks — without this, picking a different agent from the dropdown
    // on an existing chat was silently discarded on every send.
    chat = await prisma.chat.update({ where: { id: chat.id }, data: { model } });
  }

  await prisma.message.create({ data: { chatId: chat.id, role: "user", content } });

  return runAssistantReply(chat.id, chat.model);
}
