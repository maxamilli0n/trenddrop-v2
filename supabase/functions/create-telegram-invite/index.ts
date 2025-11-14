// deno-lint-ignore-file no-explicit-any
import { serve } from "https://deno.land/std@0.224.0/http/server.ts";
import { db, telegramBotToken, telegramCommunityChatId } from "../_shared/config.ts";

async function createInviteLink(chatId: string, botToken: string): Promise<string> {
  const expire = Math.floor(Date.now() / 1000) + 7 * 24 * 3600;
  const payload = { chat_id: chatId, member_limit: 1, expire_date: expire };
  const r = await fetch(`https://api.telegram.org/bot${botToken}/createChatInviteLink`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await r.json();
  if (!data?.ok) throw new Error(`telegram error: ${r.status} ${JSON.stringify(data)}`);
  return data.result.invite_link as string;
}

serve(async (req) => {
  try {
    if (req.method !== "POST") return new Response("Method Not Allowed", { status: 405 });
    const body = await req.json();
    const email = (body?.email || "").toString().trim().toLowerCase();
    const purchase_id = (body?.purchase_id || "").toString().trim();
    if (!email) return new Response(JSON.stringify({ error: "email required" }), { status: 400 });

    const supabase = db;
    if (!supabase) return new Response(JSON.stringify({ error: "supabase not configured" }), { status: 500 });

    let query = supabase.from("subscribers").select("id, status, claimed_at").eq("email", email);
    if (purchase_id) query = query.eq("purchase_id", purchase_id);
    const { data: subs, error } = await query.limit(1).maybeSingle();
    if (error) return new Response(JSON.stringify({ error: error.message }), { status: 500 });
    if (!subs) return new Response(JSON.stringify({ error: "no account found" }), { status: 404 });
    if (subs.status !== "paid") return new Response(JSON.stringify({ error: subs.status }), { status: 403 });
    if (subs.claimed_at) return new Response(JSON.stringify({ error: "already claimed" }), { status: 409 });

    const bot = telegramBotToken;
    const chat = telegramCommunityChatId;
    if (!bot || !chat) return new Response(JSON.stringify({ error: "telegram not configured" }), { status: 500 });

    const invite_link = await createInviteLink(chat, bot);
    const { error: upErr } = await supabase
      .from("subscribers")
      .update({ claimed_at: new Date().toISOString() })
      .eq("id", subs.id);
    if (upErr) return new Response(JSON.stringify({ error: upErr.message }), { status: 500 });

    return new Response(JSON.stringify({ invite_link }), { headers: { "content-type": "application/json" } });
  } catch (e) {
    return new Response(JSON.stringify({ error: String(e) }), { status: 500 });
  }
});


