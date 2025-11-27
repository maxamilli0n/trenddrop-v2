// supabase/functions/create-checkout-session/index.ts
import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import Stripe from "https://esm.sh/stripe@16.6.0?target=deno";
import { mode, stripeSecretKey } from "../_shared/config.ts";

type CreateCheckoutPayload = {
  priceId?: string;
  successUrl?: string;
  cancelUrl?: string;
  planId?: string;
  productName?: string;
  mode?: "subscription" | "payment";
  customerEmail?: string;
};

const corsHeaders = {
  "access-control-allow-origin": "*",
  "access-control-allow-headers": "authorization, x-client-info, apikey, content-type",
};

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...corsHeaders },
  });
}

const stripe = stripeSecretKey
  ? new Stripe(stripeSecretKey, {
      apiVersion: "2023-10-16",
    })
  : null;

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }

  if (req.method !== "POST") {
    return json(405, { ok: false, error: "Method Not Allowed" });
  }

  if (!stripe) {
    console.error("[checkout] Stripe secret key missing");
    return json(500, { ok: false, error: "Stripe not configured" });
  }

  let payload: CreateCheckoutPayload = {};
  try {
    payload = await req.json();
  } catch {
    return json(400, { ok: false, error: "Invalid JSON body" });
  }

  const priceId = payload.priceId?.trim();
  const successUrl = payload.successUrl?.trim();
  const cancelUrl = payload.cancelUrl?.trim();
  if (!priceId || !successUrl || !cancelUrl) {
    return json(400, { ok: false, error: "priceId, successUrl, cancelUrl are required" });
  }

  const planId = payload.planId?.trim() || "premium_telegram";
  const productName = payload.productName?.trim() || "TrendDrop Premium Access";
  const checkoutMode = payload.mode ?? "subscription";

  try {
    const session = await stripe.checkout.sessions.create({
      mode: checkoutMode,
      line_items: [
        {
          price: priceId,
          quantity: 1,
        },
      ],
      success_url: successUrl,
      cancel_url: cancelUrl,
      metadata: {
        plan_id: planId,
        product_name: productName,
        trenddrop_mode: mode,
      },
      customer_email: payload.customerEmail?.trim() || undefined,
      allow_promotion_codes: true,
      subscription_data: checkoutMode === "subscription"
        ? { metadata: { plan_id: planId, product_name: productName } }
        : undefined,
    });

    if (!session?.url) {
      return json(500, { ok: false, error: "Stripe did not return a URL" });
    }

    return json(200, { ok: true, url: session.url, id: session.id });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error("[checkout] error creating session", message);
    return json(500, { ok: false, error: message });
  }
});


