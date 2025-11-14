// supabase/edge-functions/products-report/index.ts
// Returns product feeds as JSON or CSV.
// Usage examples are at the bottom of this file.

import { serve } from "https://deno.land/std@0.224.0/http/server.ts";
import { db } from "../../_shared/config.ts";

type Row = {
  id: string;
  title: string | null;
  price: number | null;
  currency: string | null;
  image_url: string | null;
  url: string | null;
  keyword: string | null;
  seller_feedback: number | null;
  top_rated: boolean | null;
  provider: string | null;
  source: string | null;
  created_at: string;
};

function supa() { if (!db) throw new Error("supabase not configured"); return db; }

function toCSV(rows: Row[]): string {
  if (!rows.length) return "";
  const headers = Object.keys(rows[0]) as (keyof Row)[];
  const headerLine = headers.join(",");
  const esc = (v: unknown) =>
    v == null ? "" : String(v).includes(",") || String(v).includes("\"") || String(v).includes("\n")
      ? `"${String(v).replaceAll("\"", "\"\"")}` + `"`
      : String(v);
  const dataLines = rows.map((r) => headers.map((h) => esc((r as any)[h])).join(","));
  return [headerLine, ...dataLines].join("\n");
}

serve(async (req) => {
  try {
    const url = new URL(req.url);
    const type   = (url.searchParams.get("type") || "top").toLowerCase(); // top | recent | search
    const format = (url.searchParams.get("format") || "json").toLowerCase(); // json | csv
    const q      = url.searchParams.get("q") || "";
    const minFb  = Number(url.searchParams.get("min_feedback") || "0");
    const days   = Number(url.searchParams.get("days") || (type === "recent" ? "7" : "0"));
    const limit  = Math.min(Number(url.searchParams.get("limit") || "200"), 1000);

    const s = supa();
    let rows: Row[] = [];

    if (type === "top") {
      // v_products_top_by_feedback
      const { data, error } = await s
        .from("v_products_top_by_feedback")
        .select("*")
        .limit(limit);
      if (error) throw error;
      rows = (data || []) as Row[];
    } else if (type === "recent") {
      // v_products_recent_7d (adjust days via RPC if needed)
      if (days === 7) {
        const { data, error } = await s
          .from("v_products_recent_7d")
          .select("*")
          .limit(limit);
        if (error) throw error;
        rows = (data || []) as Row[];
      } else {
        // Use RPC to respect custom days
        const { data, error } = await s.rpc("products_by_keyword", {
          q: "",
          min_feedback: 0,
          days,
          max_rows: limit,
        });
        if (error) throw error;
        rows = (data || []) as Row[];
      }
    } else {
      // search → products_by_keyword
      const { data, error } = await s.rpc("products_by_keyword", {
        q,
        min_feedback: minFb,
        days,
        max_rows: limit,
      });
      if (error) throw error;
      rows = (data || []) as Row[];
    }

    if (format === "csv") {
      const csv = toCSV(rows);
      return new Response(csv, {
        status: 200,
        headers: {
          "content-type": "text/csv; charset=utf-8",
          "cache-control": "public, max-age=60",
        },
      });
    }

    return new Response(JSON.stringify({ ok: true, count: rows.length, rows }, null, 2), {
      status: 200,
      headers: {
        "content-type": "application/json",
        "cache-control": "public, max-age=30",
      },
    });
  } catch (e) {
    return new Response(JSON.stringify({ ok: false, error: String(e) }), {
      status: 500,
      headers: { "content-type": "application/json" },
    });
  }
});


