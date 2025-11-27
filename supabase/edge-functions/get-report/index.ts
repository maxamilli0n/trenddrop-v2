// deno-lint-ignore-file no-explicit-any
import { serve } from "https://deno.land/std@0.224.0/http/server.ts";
import { db } from "../../functions/_shared/config.ts";

const supabase = db;

serve(async (req) => {
  try {
    if (!supabase) return new Response("supabase not configured", { status: 500 });
    const url = new URL(req.url);
    const email = url.searchParams.get("email") || "";
    const product = (url.searchParams.get("product") || "weekly-report").trim();
    if (!email) return new Response("missing email", { status: 400 });

    // entitlement check
    const { data: ent, error: entErr } = await supabase
      .from("entitlements")
      .select("*")
      .eq("email", email)
      .eq("product_key", product)
      .maybeSingle();
    if (entErr) return new Response("error", { status: 500 });
    if (!ent) return new Response("no entitlement", { status: 403 });

    // list latest file in reports/weekly/
    const { data: list, error: listErr } = await supabase.storage
      .from("reports")
      .list("weekly", { sortBy: { column: "created_at", order: "desc" } });
    if (listErr || !list?.length) return new Response("no reports", { status: 404 });
    const latest = `weekly/${list[0].name}`;

    const { data: sign, error: signErr } = await supabase.storage
      .from("reports")
      .createSignedUrl(latest, 60 * 60);
    if (signErr || !sign?.signedUrl) return new Response("sign failed", { status: 500 });

    return new Response(JSON.stringify({ url: sign.signedUrl }), {
      headers: { "content-type": "application/json" },
      status: 200,
    });
  } catch (e) {
    console.error(e);
    return new Response("error", { status: 500 });
  }
});


