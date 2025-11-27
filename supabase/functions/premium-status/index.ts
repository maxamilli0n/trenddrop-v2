// supabase/functions/premium-status/index.ts

import { createClient } from "https://esm.sh/@supabase/supabase-js@2.48.0";

// ----- Supabase client (service role) -----

const SUPABASE_URL = Deno.env.get("SUPABASE_URL") ?? "";
const SUPABASE_SERVICE_ROLE_KEY =
  Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";

const db =
  SUPABASE_URL && SUPABASE_SERVICE_ROLE_KEY
    ? createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, {
        auth: { persistSession: false },
      })
    : null;

// ----- Helpers -----

function jsonResponse(body: unknown, status = 200): Response {
  const headers = new Headers({
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
  });

  return new Response(JSON.stringify(body), { status, headers });
}

async function getEmailFromRequest(req: Request): Promise<string | null> {
  const url = new URL(req.url);

  // 1) Try query param first (?email=...)
  const qp = url.searchParams.get("email");
  if (qp && qp.trim()) return qp.trim().toLowerCase();

  // 2) For POST, try JSON body
  if (req.method === "POST") {
    try {
      if (
        req.headers.get("content-type")?.includes("application/json")
      ) {
        const body = (await req.json()) as { email?: string };
        if (body?.email && body.email.trim()) {
          return body.email.trim().toLowerCase();
        }
      }
    } catch {
      // ignore body parse errors
    }
  }

  return null;
}

// ----- Main handler -----

Deno.serve(async (req: Request) => {
  // CORS preflight
  if (req.method === "OPTIONS") {
    return jsonResponse({}, 200);
  }

  // If env not wired we fail fast
  if (!db) {
    console.error("[premium-status] Supabase client not configured");
    return jsonResponse(
      { error: "server_not_configured", isPremium: false, member: null },
      500,
    );
  }

  // Health check if no email on GET
  const email = await getEmailFromRequest(req);
  if (!email && req.method === "GET") {
    return jsonResponse({ status: "ok", service: "premium-status" }, 200);
  }

  if (!email) {
    return jsonResponse(
      { error: "missing_email", isPremium: false, member: null },
      400,
    );
  }

  // ----- Query v_premium_members -----
  try {
    const { data, error } = await db
      .from("v_premium_members")
      .select("*")
      .eq("email", email)
      .eq("status", "active")
      .order("last_event_at", { ascending: false })
      .limit(1);

    if (error) {
      console.error("[premium-status] db error", error);
      return jsonResponse(
        { error: "db_error", isPremium: false, member: null },
        500,
      );
    }

    const member = data && data.length > 0 ? data[0] : null;
    const isPremium = !!member;

    return jsonResponse({ isPremium, member }, 200);
  } catch (err) {
    console.error(
      "[premium-status] unexpected error",
      err instanceof Error ? err.message : String(err),
    );
    return jsonResponse(
      { error: "unexpected_error", isPremium: false, member: null },
      500,
    );
  }
});

