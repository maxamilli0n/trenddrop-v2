// @ts-nocheck
import { serve } from "https://deno.land/std@0.224.0/http/server.ts";
import { db, gumroadWebhookSecret, gumroadSellerId, requireGumroadAuth, telegramBotToken, telegramCommunityChatId, supabaseUrl, supabaseServiceRoleKey, reportsBucket } from "../_shared/config.ts";
import { crypto } from "https://deno.land/std@0.224.0/crypto/mod.ts";

function supa() { if (!db) throw new Error("supabase not configured"); return db; }

function parseForm(body: string) {
  const out: Record<string, unknown> = {};
  for (const [k, v] of new URLSearchParams(body).entries()) out[k] = v;
  return out;
}

async function parseBody(req: Request) {
  const ct = (req.headers.get("content-type") || "").toLowerCase();
  if (ct.includes("application/json")) return await req.json();
  const raw = await req.text();
  if (ct.includes("application/x-www-form-urlencoded")) return parseForm(raw);
  try { return JSON.parse(raw); } catch { return { raw }; }
}

const ON = (s?: string) => (s || "").toLowerCase() === "true";
const actionOf = (p: Record<string, unknown>) =>
  String(p["action"] ?? p["resource_name"] ?? "").toLowerCase();

/** HMAC check: X-Gumroad-Signature = hex( HMAC_SHA256(secret, rawBody) ) */
async function verifyHmac(req: Request, rawBody: string): Promise<boolean> {
  const secret = gumroadWebhookSecret;
  if (!secret) return false;

  const header =
    req.headers.get("X-Gumroad-Signature") ??
    req.headers.get("x-gumroad-signature") ??
    "";

  if (!header) return false;

  // Compute HMAC over RAW body string
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sigBytes = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(rawBody));
  const computed = Array.from(new Uint8Array(sigBytes))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");

  // Some senders send uppercase; compare case-insensitive
  return computed.toLowerCase() === header.trim().toLowerCase();
}

async function handleSale(p: Record<string, any>) {
  const s = supa();

  const sale_id   = String(p["sale_id"] ?? p["id"] ?? "");
  const email     = String(p["email"] ?? p["purchaser_email"] ?? "");
  const productId = String(p["product_id"] ?? p["product"]?.id ?? "");
  const sellerId  = String(p["seller_id"] ?? p["seller"]?.id ?? "");
  const fullName  = String(p["full_name"] ?? p["purchaser_name"] ?? "");

  if (!sale_id || !email) {
    return { ok: false, error: "missing_sale_or_email" } as const;
  }

  // Idempotent write
  const { error } = await s
    .from("subscribers")
    .upsert(
      {
        sale_id,
        email,
        product_id: productId || null,
        seller_id:  sellerId  || null,
        full_name:  fullName  || null,
      },
      { onConflict: "sale_id" },
    );

  if (error) return { ok: false, error: error.message } as const;

  // Optional Telegram notify
  try {
    const bot  = telegramBotToken;
    const chat = telegramCommunityChatId;
    if (bot && chat) {
      const text = `✅ New TrendDrop+ subscriber: ${email} (product ${productId || "n/a"})`;
      await fetch(`https://api.telegram.org/bot${bot}/sendMessage`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ chat_id: chat, text }),
      });
    }
  } catch (e) {
    console.warn("[gumroad] telegram_notify_failed", String(e));
  }

  // Optional auto-invite (single block, deduped)
  try {
    const rawTg = String(
      p?.telegram_user_id ??
      p?.custom_fields?.telegram_user_id ??
      p?.telegram_id ??
      p?.custom_fields?.telegram_id ??
      p?.telegram ?? "",
    ).replace(/^@+/, "");
    const tgId = Number(rawTg);
    const supaUrl = supabaseUrl;
    const svcKey  = supabaseServiceRoleKey;
    if (rawTg && Number.isFinite(tgId) && supaUrl && svcKey) {
      const r = await fetch(`${supaUrl}/functions/v1/add-telegram-member`, {
        method: "POST",
        headers: { "content-type": "application/json", authorization: `Bearer ${svcKey}` },
        body: JSON.stringify({ telegram_user_id: tgId }),
      });
      if (!r.ok) console.warn("[gumroad] add-telegram-member failed", r.status);
    }
  } catch (e) {
    console.warn("[gumroad] telegram_auto_invite_failed", String(e));
  }

  // Attach 7-day signed links to latest artifacts (best-effort)
  const bucket = reportsBucket;
  const expiresIn = 60 * 60 * 24 * 7;
  const pdfKey = "weekly/latest.pdf";
  const csvKey = "weekly/latest.csv";

  let pdf_url: string | null = null;
  let csv_url: string | null = null;
  try {
    const { data } = await s.storage.from(bucket).createSignedUrl(pdfKey, expiresIn);
    pdf_url = data?.signedUrl ?? null;
  } catch {}
  try {
    const { data } = await s.storage.from(bucket).createSignedUrl(csvKey, expiresIn);
    csv_url = data?.signedUrl ?? null;
  } catch {}
  try {
    await s.from("subscribers").update({ pdf_url, csv_url }).eq("sale_id", sale_id);
  } catch {}

  return { ok: true, pdf_url, csv_url } as const;
}

serve(async (req) => {
  // --- Read RAW body first (needed for HMAC) ---
  const rawBody = await req.text();
  // Reconstruct a request with a tee’d body for downstream parsing
  const cloned = new Request(req, { body: rawBody });

  const payload = await parseBody(cloned);
  const action  = actionOf(payload);

  // Pings always OK (so Gumroad “Test” passes)
  if (action === "ping") {
    return new Response(JSON.stringify({ ok: true, type: "ping" }), {
      status: 200, headers: { "content-type": "application/json" },
    });
  }

  // Optional auth
  if (requireGumroadAuth) {
    const needSecret = gumroadWebhookSecret;
    const needSeller = gumroadSellerId;

    // Prefer header HMAC if present
    let authed = false;
    try { authed = await verifyHmac(req, rawBody); } catch {}

    // Fallback to body secret/seller_id
    if (!authed) {
      const gotSecret =
        (payload as any)?.secret ??
        (payload as any)?.custom_fields?.secret;
      const gotSeller =
        (payload as any)?.seller_id ??
        (payload as any)?.seller?.id;

      authed =
        (!!needSecret ? gotSecret === needSecret : true) &&
        (!!needSeller ? gotSeller === needSeller : true);
    }

    if (!authed) {
      // If you want Gumroad to retry on bad auth, return 401 instead of 200:
      // return new Response(JSON.stringify({ ok: false, error: "unauthorized" }), { status: 401, headers: { "content-type": "application/json" }});
      return new Response(JSON.stringify({ ok: false, error: "unauthorized" }), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    }
  }

  if (action === "sale") {
    const result = await handleSale(payload as any);
    return new Response(JSON.stringify(result), {
      status: 200, headers: { "content-type": "application/json" },
    });
  }

  // Refund / chargeback / cancellation → mark revoked_at
  if (action === "refund" || action === "chargeback" || action === "subscription_cancelled") {
    try {
      const s = supa();
      const sale_id = String((payload as any)?.sale_id ?? (payload as any)?.id ?? "");
      if (sale_id) {
        await s.from("subscribers").update({ revoked_at: new Date().toISOString() }).eq("sale_id", sale_id);
      }
    } catch (e) {
      console.warn("[gumroad] revoke_failed", String(e));
    }
    return new Response(JSON.stringify({ ok: true }), {
      status: 200, headers: { "content-type": "application/json" },
    });
  }

  console.log("[gumroad] event", {
    action,
    email: (payload as any)?.email,
    seller_id_present: !!((payload as any)?.seller_id || (payload as any)?.seller?.id),
  });

  return new Response(JSON.stringify({ ok: true, received: action || "unknown" }), {
    status: 200, headers: { "content-type": "application/json" },
  });
});


