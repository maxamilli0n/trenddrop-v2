// TD-AUTO: BEGIN api-products
// deno-lint-ignore-file no-explicit-any
import { serve } from "https://deno.land/std@0.224.0/http/server.ts";
import { db } from "../../_shared/config.ts";

function supa() { if (!db) throw new Error("supabase not configured"); return db; }

function authed(req: Request): boolean {
  const a = req.headers.get("authorization") || req.headers.get("Authorization");
  return !!a;
}

serve(async (req) => {
  if (!authed(req)) return new Response(JSON.stringify({ ok: false, error: "unauthorized" }), { status: 401 });
  const url = new URL(req.url);
  const limit = Math.max(1, Math.min(200, Number(url.searchParams.get("limit") || 50)));
  const category = url.searchParams.get("category") || undefined;

  const s = supa();
  let query = s.from("v_products_top").select("*").limit(limit);
  if (category) query = query.eq("category", category);
  const { data, error } = await query;
  if (error) {
    // Fallback to products
    const f = await s.from("products").select("*").limit(limit).eq("category", category || "");
    return new Response(JSON.stringify({ ok: notNull(f.data), data: f.data || [], error: error.message }), { headers: { "content-type": "application/json" } });
  }
  return new Response(JSON.stringify({ ok: true, data }), { headers: { "content-type": "application/json" } });
});

function notNull<T>(v: T | null): v is T { return v !== null; }
// TD-AUTO: END api-products


