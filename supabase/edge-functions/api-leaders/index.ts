// TD-AUTO: BEGIN api-leaders
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
  const limit = Math.max(1, Math.min(100, Number(url.searchParams.get("limit") || 20)));
  const by = (url.searchParams.get("by") || "category").toLowerCase();

  const s = supa();
  const column = by === "brand" ? "brand" : "category";
  const { data, error } = await s.rpc("td_leaders_agg", { col: column, lim: limit });
  if (!error && Array.isArray(data)) {
    return new Response(JSON.stringify({ ok: true, data }), { headers: { "content-type": "application/json" } });
  }
  // Fallback simple group-by via view products (limited by PostgREST capabilities)
  const { data: items } = await s.from("products").select(`${column}`).limit(1000);
  const map = new Map<string, number>();
  for (const it of items || []) {
    const key = String((it as any)[column] || "unknown");
    map.set(key, (map.get(key) || 0) + 1);
  }
  const leaders = Array.from(map.entries()).sort((a, b) => b[1] - a[1]).slice(0, limit).map(([name, count]) => ({ name, count }));
  return new Response(JSON.stringify({ ok: true, data: leaders }), { headers: { "content-type": "application/json" } });
});
// TD-AUTO: END api-leaders


