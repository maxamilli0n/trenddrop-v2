// supabase/functions/stripe-webhook/index.ts

import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient as createSupabaseClient } from "jsr:@supabase/supabase-js@2.46.1";

/**
 * 1) ENV
 * - MODE: "live" | "test"  (controls which signing secret we use)
 * - STRIPE_WEBHOOK_SECRET_LIVE / STRIPE_WEBHOOK_SECRET_TEST
 * - BREVO_API_KEY
 * - EMAIL_FROM                (verified Brevo sender, e.g. "Your Brand <sender@domain>")
 * - TELEGRAM_INVITE_URL
 * - SUPABASE_URL
 * - SUPABASE_SERVICE_ROLE_KEY
 */
const mode = (Deno.env.get("MODE") ?? "live").toLowerCase();
const STRIPE_SECRET =
  mode === "live"
    ? Deno.env.get("STRIPE_WEBHOOK_SECRET_LIVE")
    : Deno.env.get("STRIPE_WEBHOOK_SECRET_TEST");

const BREVO_KEY = Deno.env.get("BREVO_API_KEY");
const EMAIL_FROM = Deno.env.get("EMAIL_FROM")!;
const TG_URL = Deno.env.get("TELEGRAM_INVITE_URL") ?? "https://t.me/+yourInvite";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL") ?? "";
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
const db = SUPABASE_URL && SUPABASE_SERVICE_ROLE_KEY
  ? createSupabaseClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, {
      auth: { persistSession: false },
    })
  : null;

function bad(msg: string, status = 400) {
  return new Response(JSON.stringify({ ok: false, error: msg }), {
    status,
    headers: { "content-type": "application/json" },
  });
}
function ok(data?: unknown) {
  return new Response(JSON.stringify(data ?? { ok: true }), {
    headers: { "content-type": "application/json" },
  });
}

/** timing-safe equality */
function tsec(a: string, b: string) {
  if (a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}

/** HMAC SHA-256 = hex */
async function hmacSHA256Hex(secret: string, message: string) {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    enc.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sigBuf = await crypto.subtle.sign("HMAC", key, enc.encode(message));
  const bytes = new Uint8Array(sigBuf);
  return Array.from(bytes).map(b => b.toString(16).padStart(2, "0")).join("");
}

/** Stripe `Stripe-Signature` header parser */
function parseStripeSigHeader(header: string | null) {
  if (!header) return null;
  const parts = header.split(",").map(s => s.trim());
  let t: string | null = null;
  const v1: string[] = [];
  for (const p of parts) {
    const [k, v] = p.split("=");
    if (k === "t") t = v;
    if (k === "v1") v1.push(v);
  }
  if (!t || v1.length === 0) return null;
  return { t, v1 };
}

/** Verify Stripe signature WITHOUT the Stripe SDK */
async function verifyStripeSignature(rawBody: string, sigHeader: string, secret: string) {
  const parsed = parseStripeSigHeader(sigHeader);
  if (!parsed) return false;

  const signedPayload = `${parsed.t}.${rawBody}`;
  const expected = await hmacSHA256Hex(secret, signedPayload);

  // any v1 match is OK
  return parsed.v1.some((given) => tsec(given, expected));
}

/** Email template */
function renderEmailHTML(productName: string, inviteUrl: string) {
  const title = productName || "Premium Access";
  return `<!doctype html>
<html><head><meta charset="utf-8"/>
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>Your access to ${title}</title>
<style>
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:0;padding:0;background:#0b0b0b;color:#fff}
.wrap{max-width:560px;margin:0 auto;padding:32px}
.card{background:#121212;border:1px solid #222;border-radius:14px;padding:28px}
h1{font-size:22px;margin:0 0 10px}
p{line-height:1.55;color:#cfd3d8}
.btn{display:inline-block;margin-top:16px;padding:12px 18px;border-radius:10px;background:#16a34a;color:#fff !important;text-decoration:none;font-weight:600}
.sub{font-size:12px;color:#9aa2b1;margin-top:18px}
a{color:#7dd3fc}
</style></head>
<body><div class="wrap"><div class="card">
<h1>Welcome to ${title} 🎉</h1>
<p>You’re in. Click the button below to join the private community.</p>
<p><a class="btn" href="${inviteUrl}" target="_blank" rel="noopener">Join Premium</a></p>
<p class="sub">If the button doesn’t work, open this link:<br><a href="${inviteUrl}">${inviteUrl}</a></p>
</div>
<p class="sub">You’re receiving this because a purchase or subscription completed on TrendDrop Studio.</p>
</div></body></html>`;
}

/** extract product name (best-effort) */
function extractProductName(evt: any): string {
  const obj = evt?.data?.object ?? {};
  const metaName = obj?.metadata?.product_name;
  if (metaName) return String(metaName);
  const line = obj?.display_items?.[0];
  const planName = line?.plan?.nickname ?? line?.price?.nickname ?? "";
  if (planName) return String(planName);
  return "Premium Access";
}

/** try to find buyer email across shapes */
function extractBuyerEmail(evt: any): string | null {
  const obj = evt?.data?.object;
  switch (evt?.type) {
    case "checkout.session.completed":
      return (
        obj?.customer_details?.email ??
        obj?.customer_email ??
        obj?.customer?.email ??
        null
      );
    case "payment_intent.succeeded":
      return (
        obj?.receipt_email ??
        obj?.customer?.email ??
        obj?.latest_charge?.billing_details?.email ??
        null
      );
    case "invoice.paid":
      return (
        obj?.customer_email ??
        obj?.customer?.email ??
        obj?.account_customer_email ??
        null
      );
    default:
      return null;
  }
}

// -------------------- Normalization --------------------
type NormalizedStripeInfo = {
  eventId: string;
  customerId: string | null;
  checkoutSessionId: string | null;
  email: string | null;
  productName: string;
  planId: string | null;
};

function normalizeStripeEvent(evt: any): NormalizedStripeInfo {
  const obj = evt?.data?.object ?? {};
  const type = evt?.type ?? "";

  const base: NormalizedStripeInfo = {
    eventId: String(evt?.id ?? ""),
    customerId: null,
    checkoutSessionId: null,
    email: null,
    productName: extractProductName(evt),
    planId: null,
  };

  if (type === "checkout.session.completed") {
    base.customerId = obj?.customer ?? null;
    base.checkoutSessionId = obj?.id ?? null;
    base.email =
      obj?.customer_details?.email ??
      obj?.customer_email ??
      obj?.customer?.email ??
      null;
    base.planId =
      obj?.metadata?.plan_id ??
      obj?.metadata?.product_name ??
      null;
    return base;
  }

  if (type === "payment_intent.succeeded") {
    base.customerId = obj?.customer ?? null;
    base.checkoutSessionId =
      obj?.metadata?.checkout_session_id ??
      obj?.latest_charge ??
      null;
    const charges0 = obj?.charges?.data?.[0];
    base.email =
      charges0?.billing_details?.email ??
      obj?.receipt_email ??
      obj?.customer?.email ??
      null;
    base.planId =
      obj?.metadata?.plan_id ??
      obj?.metadata?.product_name ??
      null;
    return base;
  }

  // Reasonable defaults for other event types
  return base;
}

async function sendWithBrevo(to: string, subject: string, html: string) {
  const match = EMAIL_FROM.match(/^(.*)<(.+@.+)>$/);
  const sender = match
    ? { name: match[1].trim(), email: match[2].trim() }
    : { name: EMAIL_FROM, email: EMAIL_FROM };

  const res = await fetch("https://api.brevo.com/v3/smtp/email", {
    method: "POST",
    headers: {
      accept: "application/json",
      "content-type": "application/json",
      "api-key": BREVO_KEY!,
    },
    body: JSON.stringify({
      sender,
      to: [{ email: to }],
      subject,
      htmlContent: html,
    }),
  });

  if (!res.ok) {
    const text = await res.text();
    console.error("[brevo] send failed", res.status, text);
    throw new Error(`Brevo error ${res.status}`);
  }
}

type BrevoResult = { ok: true } | { ok: false; error: string };
async function sendOnboardingEmail(to: string, productName: string): Promise<BrevoResult> {
  try {
    const subject = `Your access to ${productName}`;
    const html = renderEmailHTML(productName, TG_URL);
    await sendWithBrevo(to, subject, html);
    return { ok: true };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.error("[brevo] error", message);
    return { ok: false, error: message };
  }
}

// -------------------- Handler --------------------
Deno.serve(async (req) => {
  try {
    // Health check
    if (req.method === "GET") return ok({ status: "ok" });

    if (!STRIPE_SECRET) return bad("server not configured (secret)", 500);
    if (!BREVO_KEY || !EMAIL_FROM) return bad("server not configured (email)", 500);

    // IMPORTANT: get raw body FIRST
    const raw = await req.text();

    // Verify signature without the Stripe SDK
    const sigHeader = req.headers.get("stripe-signature");
    if (!sigHeader) return bad("missing signature", 400);

    const valid = await verifyStripeSignature(raw, sigHeader, STRIPE_SECRET);
    if (!valid) {
      console.warn("[stripe-webhook] signature verification failed");
      return bad("bad signature", 400);
    }

    // Now it’s safe to parse
    const evt = JSON.parse(raw);

    // Idempotency via Postgres (no Deno.openKv)
    if (db) {
      const ledgerUpsert = await db
        .from("stripe_event_ledger")
        .upsert(
          { event_id: evt.id, event_type: evt.type },
          { onConflict: "event_id", ignoreDuplicates: true },
        )
        .select("event_id");

      if (ledgerUpsert.error) {
        console.error("[stripe-webhook] ledger upsert error", ledgerUpsert.error);
        // continue; do not fail the webhook
      } else if ((ledgerUpsert.data ?? []).length === 0) {
        console.log(`[stripe-webhook] duplicate event ${evt.id}, skipping`);
        return ok({ received: true, duplicate: true });
      }
    } else {
      console.error("[stripe-webhook] db not configured, skipping DB writes (ledger)");
    }

    // Only these events send the email
    const shouldSend =
      evt.type === "checkout.session.completed" ||
      evt.type === "payment_intent.succeeded" ||
      evt.type === "invoice.paid";

    if (!shouldSend) {
      console.log("[stripe-webhook] event ignored:", evt.type);
      return ok({ ignored: true });
    }

    // Normalize once
    const normalized = normalizeStripeEvent(evt);
    const buyerEmail = normalized.email ?? extractBuyerEmail(evt);
    if (!buyerEmail) {
      console.log("[stripe-webhook] no buyer email; skipping send. type:", evt.type);
      return ok({ skipped: true, reason: "no_email" });
    }

    // DB writes for successful checkout events
    const shouldProcessDb =
      evt.type === "checkout.session.completed" ||
      evt.type === "payment_intent.succeeded";
    let customerRow: any | null = null;
    if (db && shouldProcessDb) {
      try {
        // 1) Upsert customer
        const customerRes = await db
          .from("customers")
          .upsert(
            {
              stripe_customer_id: normalized.customerId,
              email: buyerEmail ?? "",
            },
            {
              onConflict: "stripe_customer_id",
              ignoreDuplicates: false,
            },
          )
          .select("*")
          .single();
        if (customerRes.error) {
          console.error("[stripe-webhook] db error (customers.upsert)", customerRes.error);
        } else {
          customerRow = customerRes.data ?? null;
        }

        // 2) Upsert premium_subscriptions
        const subRes = await db
          .from("premium_subscriptions")
          .upsert(
            {
              customer_id: customerRow?.id ?? null,
              stripe_subscription_id: null,
              stripe_checkout_session_id: normalized.checkoutSessionId,
              status: "active",
              plan_id: normalized.planId ?? normalized.productName,
              last_event_at: new Date().toISOString(),
            },
            {
              onConflict: "stripe_checkout_session_id",
              ignoreDuplicates: false,
            },
          );
        if (subRes.error) {
          console.error("[stripe-webhook] db error (premium_subscriptions.upsert)", subRes.error);
        }
      } catch (err) {
        console.error("[stripe-webhook] db error (upserts)", err);
      }
    } else if (!db) {
      console.error("[stripe-webhook] db not configured, skipping DB upserts");
    }

    // Brevo + onboarding_emails linkage
    const brevo = await sendOnboardingEmail(buyerEmail, normalized.productName);

    if (db) {
      const ins = await db.from("onboarding_emails").insert({
        to_email: buyerEmail,
        product_name: normalized.productName,
        event_id: normalized.eventId,
        stripe_checkout_session_id: normalized.checkoutSessionId,
        stripe_customer_id: normalized.customerId,
        status: brevo.ok ? "sent" : "error",
        error_message: brevo.ok ? null : brevo.error,
      });
      if (ins.error) {
        console.error("[stripe-webhook] db error (onboarding_emails.insert)", ins.error);
      }
    } else {
      console.error("[stripe-webhook] db not configured, skipping onboarding_emails insert");
    }

    console.log("[stripe-webhook] onboarding email processed ->", buyerEmail);
    return ok({ received: true, sent: brevo.ok });
  } catch (err) {
    console.error("[stripe-webhook] unhandled", err);
    return bad("internal error", 500);
  }
});

