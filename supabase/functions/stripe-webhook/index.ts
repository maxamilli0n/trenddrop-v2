// supabase/functions/stripe-webhook/index.ts

import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import {
  db,
  stripeWebhookSecret,
  brevoApiKey,
  emailFrom,
  telegramInviteUrl,
} from "../_shared/config.ts";

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
const WEBHOOK_SECRET = stripeWebhookSecret;
const EMAIL_FROM = emailFrom || "";
const TG_URL = telegramInviteUrl || "https://t.me/+yourInvite";

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

const BRAND_BG = "#050813";
const CARD_BG = "#0f172a";
const TEXT = "#e5edff";
const MUTED = "#9ca3af";
const ACCENT = "#38bdf8";
const BUTTON = "#22c55e";

/** Email template */
function renderEmailHTML(productName: string, tgUrl: string): string {
  const safeProduct = productName || "TrendDrop Premium Access";

  return `
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Your access to ${safeProduct}</title>
  <meta name="viewport" content="width=device-width,initial-scale=1" />
</head>
<body style="margin:0;padding:0;background:${BRAND_BG};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="background:${BRAND_BG};padding:32px 16px;">
    <tr>
      <td align="center">
        <table width="100%" cellpadding="0" cellspacing="0" role="presentation" style="max-width:560px;background:${CARD_BG};border-radius:18px;overflow:hidden;border:1px solid #1e293b;">
          <!-- Header -->
          <tr>
            <td style="padding:20px 24px 12px 24px;border-bottom:1px solid #1e293b;">
              <table width="100%" cellpadding="0" cellspacing="0" role="presentation">
                <tr>
                  <td align="left">
                    <div style="display:flex;align-items:center;gap:10px;">
                      <span style="display:inline-block;width:32px;height:32px;border-radius:9px;background:linear-gradient(135deg,#60a5fa,#22d3ee);"></span>
                      <div style="color:${TEXT};font-size:16px;font-weight:600;">TrendDrop Studio</div>
                    </div>
                    <div style="color:${MUTED};font-size:12px;margin-top:4px;">Weekly data-driven product reports</div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- Hero -->
          <tr>
            <td style="padding:24px 24px 8px 24px;">
              <h1 style="margin:0 0 8px 0;color:${TEXT};font-size:22px;line-height:1.3;">
                Your Premium access is ready 🎉
              </h1>
              <p style="margin:0;color:${MUTED};font-size:14px;line-height:1.6;">
                Thanks for grabbing <strong style="color:${TEXT};font-weight:600;">${safeProduct}</strong>.
                Below is your button to join the private community where new drops, reports, and updates are shared first.
              </p>
            </td>
          </tr>

          <!-- CTA Button -->
          <tr>
            <td style="padding:8px 24px 16px 24px;">
              <table role="presentation" cellpadding="0" cellspacing="0">
                <tr>
                  <td align="left">
                    <a href="${tgUrl}"
                       style="display:inline-block;padding:12px 22px;border-radius:999px;background:${BUTTON};color:#020617;font-weight:600;font-size:14px;text-decoration:none;">
                      Join Premium
                    </a>
                  </td>
                </tr>
              </table>
              <p style="margin:14px 0 0 0;color:${MUTED};font-size:12px;line-height:1.5;">
                If the button doesn’t work, copy and paste this link into your browser:
                <br />
                <span style="color:${ACCENT};word-break:break-all;">${tgUrl}</span>
              </p>
            </td>
          </tr>

          <!-- What happens next -->
          <tr>
            <td style="padding:12px 24px 8px 24px;">
              <p style="margin:0 0 6px 0;color:${TEXT};font-size:14px;font-weight:600;">
                What you can expect:
              </p>
              <ul style="margin:4px 0 10px 18px;padding:0;color:${MUTED};font-size:13px;line-height:1.5;">
                <li>Access to the private Telegram channel for Premium members.</li>
                <li>Fresh trending product reports as they’re released.</li>
                <li>Quick updates if anything changes with your subscription or access.</li>
              </ul>
            </td>
          </tr>

          <!-- Help -->
          <tr>
            <td style="padding:0 24px 22px 24px;">
              <p style="margin:0;color:${MUTED};font-size:12px;line-height:1.6;">
                If you ever lose this email, you can always re-check your status using the email you paid with on the TrendDrop Studio website.
                If something looks off, reply to this message and we’ll help you out.
              </p>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="padding:12px 24px 18px 24px;border-top:1px solid #1e293b;">
              <p style="margin:0;color:${MUTED};font-size:11px;line-height:1.5;">
                You’re receiving this because a purchase or subscription was completed on
                <span style="color:${TEXT};">TrendDrop Studio</span>.
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
  `;
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
      "api-key": brevoApiKey || "",
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
    const subject = `Your TrendDrop Premium access is ready`;
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

    if (!WEBHOOK_SECRET) return bad("server not configured (secret)", 500);
    if (!brevoApiKey || !EMAIL_FROM) return bad("server not configured (email)", 500);

    // IMPORTANT: get raw body FIRST
    const raw = await req.text();

    // Verify signature without the Stripe SDK
    const sigHeader = req.headers.get("stripe-signature");
    if (!sigHeader) return bad("missing signature", 400);

    const valid = await verifyStripeSignature(raw, sigHeader, WEBHOOK_SECRET);
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

