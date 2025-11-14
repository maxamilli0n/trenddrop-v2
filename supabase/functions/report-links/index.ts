// TD-AUTO: BEGIN report-links
// deno-lint-ignore-file no-explicit-any
import { serve } from "https://deno.land/std@0.224.0/http/server.ts";
import { db, reportsBucket } from "../_shared/config.ts";

function supaService() { if (!db) throw new Error("supabase not configured"); return db; }

function requireAuth(req: Request): boolean {
  const auth = req.headers.get("authorization") || req.headers.get("Authorization");
  return !!auth; // rely on Supabase edge auth to validate JWT; presence check only
}

serve(async (req) => {
  if (req.method !== "POST") return new Response("Method Not Allowed", { status: 405 });
  if (!requireAuth(req)) return new Response(JSON.stringify({ ok: false, error: "unauthorized" }), { status: 401 });
  let body: any = {};
  try { body = await req.json(); } catch {}
  const mode = (String(body?.mode || "weekly").toLowerCase());
  const fmt  = (String(body?.format || "pdf").toLowerCase());
  const bucket = reportsBucket;

  const key = `${mode}/latest.${fmt === "csv" ? "csv" : "pdf"}`;
  const expiresIn = 60 * 60 * 24; // 24h

  try {
    const s = supaService();
    const { data, error } = await s.storage.from(bucket).createSignedUrl(key, expiresIn);
    if (error) throw error;
    return new Response(JSON.stringify({ ok: true, url: data?.signedUrl, key }), { headers: { "content-type": "application/json" } });
  } catch (e) {
    return new Response(JSON.stringify({ ok: false, error: String(e) }), { status: 500 });
  }
});
// TD-AUTO: END report-links


