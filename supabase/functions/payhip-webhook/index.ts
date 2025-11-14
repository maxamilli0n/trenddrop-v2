// TD-AUTO: BEGIN payhip-webhook
// deno-lint-ignore-file no-explicit-any
import { serve } from "https://deno.land/std@0.224.0/http/server.ts";
import { db, payhipApiKey } from "../_shared/config.ts";

function supa() { if (!db) throw new Error("supabase not configured"); return db; }

async function verifyHmac(req: Request, raw: string): Promise<boolean> {
  const secret = payhipApiKey;
  if (!secret) return false;
  const hdr = req.headers.get("X-Payhip-Signature") || req.headers.get("x-payhip-signature") || "";
  if (!hdr) return false;
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(raw));
  const computed = Array.from(new Uint8Array(sig)).map((b) => b.toString(16).padStart(2, "0")).join("");
  return computed.toLowerCase() === hdr.trim().toLowerCase();
}

async function upsert(email: string | null, attrs: Record<string, any>) {
  if (!email) return;
  const s = supa();
  const row: Record<string, any> = { email, source: "payhip" };
  if (attrs.sale_id) row.sale_id = attrs.sale_id;
  if (attrs.product_id) row.product_id = attrs.product_id;
  await s.from("subscribers").upsert(row, { onConflict: "email" });
}

serve(async (req) => {
  if (req.method !== "POST") return new Response("Method Not Allowed", { status: 405 });
  const raw = await req.text();
  const ok = await verifyHmac(req, raw);
  if (!ok) return new Response(JSON.stringify({ ok: false, error: "unauthorized" }), { status: 401 });

  let payload: any = {};
  try { payload = JSON.parse(raw); } catch { /* some send form-encoded */ }
  const event = String(payload?.event || payload?.type || "");

  if (event) {
    try {
      if (event.includes("sale") || event.includes("purchase") || event.includes("completed")) {
        const email = String(payload?.customer?.email || payload?.email || "") || null;
        await upsert(email, { sale_id: payload?.id || payload?.order_id, product_id: payload?.product_id });
      }
      if (event.includes("refund") || event.includes("chargeback")) {
        const s = supa();
        const sale_id = String(payload?.id || payload?.order_id || "");
        if (sale_id) await s.from("subscribers").update({ revoked_at: new Date().toISOString() }).eq("sale_id", sale_id);
      }
    } catch {}
  }

  return new Response(JSON.stringify({ ok: true }), { headers: { "content-type": "application/json" } });
});
// TD-AUTO: END payhip-webhook


