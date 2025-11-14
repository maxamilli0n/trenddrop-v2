// deno-lint-ignore-file no-explicit-any
import { serve } from "https://deno.land/std@0.224.0/http/server.ts";
import { telegramBotToken, telegramCommunityChatId } from "../../_shared/config.ts";

async function approveJoinRequest(chatId: string, botToken: string, userId: number) {
  const r = await fetch(`https://api.telegram.org/bot${botToken}/approveChatJoinRequest`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, user_id: userId }),
  });
  const data = await r.json();
  if (!data?.ok) throw new Error(`telegram approve error: ${r.status} ${JSON.stringify(data)}`);
  return true;
}

async function createInviteLink(chatId: string, botToken: string): Promise<string> {
  const expire = Math.floor(Date.now() / 1000) + 7 * 24 * 3600;
  const payload = { chat_id: chatId, member_limit: 1, expire_date: expire };
  const r = await fetch(`https://api.telegram.org/bot${botToken}/createChatInviteLink`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await r.json();
  if (!data?.ok) throw new Error(`telegram invite error: ${r.status} ${JSON.stringify(data)}`);
  return data.result.invite_link as string;
}

serve(async (req) => {
  try {
    if (req.method !== "POST") return new Response("Method Not Allowed", { status: 405 });
    const body = await req.json();
    const userId = Number(body?.telegram_user_id);
    if (!userId || !Number.isFinite(userId)) {
      return new Response(JSON.stringify({ ok: false, error: "invalid telegram_user_id" }), { status: 400 });
    }

    const bot = telegramBotToken;
    const chat = telegramCommunityChatId;
    if (!bot || !chat) {
      return new Response(JSON.stringify({ ok: false, error: "telegram not configured" }), { status: 500 });
    }

    try {
      await approveJoinRequest(chat, bot, userId);
      return new Response(JSON.stringify({ ok: true, approved: true }), {
        headers: { "content-type": "application/json" },
      });
    } catch (e) {
      // Fallback to invite link (user must click)
      try {
        const invite_link = await createInviteLink(chat, bot);
        return new Response(JSON.stringify({ ok: true, approved: false, invite_link }), {
          headers: { "content-type": "application/json" },
        });
      } catch (e2) {
        return new Response(JSON.stringify({ ok: false, error: String(e2) }), { status: 500 });
      }
    }
  } catch (e) {
    return new Response(JSON.stringify({ ok: false, error: String(e) }), { status: 500 });
  }
});


