// TD-AUTO: BEGIN health-ping
// deno-lint-ignore-file no-explicit-any
import { serve } from "https://deno.land/std@0.224.0/http/server.ts";
import { db, reportsBucket, supabaseUrl, supabaseServiceRoleKey, telegramBotToken, telegramAlertChatId } from "../../functions/_shared/config.ts";

function supa() { if (!db) throw new Error("supabase not configured"); return db; }

async function checkStorage(): Promise<{ ok: boolean; error?: string }> {
  try {
    const s = supa();
    const bucket = reportsBucket;
    const { data, error } = await s.storage.from(bucket).createSignedUrl("weekly/latest.pdf", 60);
    if (error) return { ok: false, error: error.message };
    return { ok: !!data?.signedUrl };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

async function checkProductsReport(): Promise<{ ok: boolean; status?: number }> {
  try {
    const url = supabaseUrl;
    const key = supabaseServiceRoleKey;
    if (!url || !key) return { ok: false };
    const r = await fetch(`${url}/functions/v1/products-report`, { headers: { authorization: `Bearer ${key}` } });
    return { ok: r.ok, status: r.status };
  } catch {
    return { ok: false };
  }
}

async function alertTelegram(msg: string) {
  try {
    const bot = telegramBotToken;
    const chat = telegramAlertChatId;
    if (!bot || !chat) return;
    await fetch(`https://api.telegram.org/bot${bot}/sendMessage`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ chat_id: chat, text: msg }),
    });
  } catch {}
}

serve(async () => {
  const storage = await checkStorage();
  const report = await checkProductsReport();
  const ok = storage.ok && report.ok;
  if (!ok) {
    await alertTelegram(`\u26a0\ufe0f Health check failed: storage=${storage.ok} products-report=${report.ok}`);
  }
  return new Response(JSON.stringify({ ok, storage, products_report: report }), {
    headers: { "content-type": "application/json" },
    status: ok ? 200 : 500,
  });
});
// TD-AUTO: END health-ping


