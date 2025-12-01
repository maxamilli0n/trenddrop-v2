// supabase/functions/products-report/index.ts

import { serve } from "https://deno.land/std@0.224.0/http/server.ts";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL") ?? "";
const SUPABASE_SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";

if (!SUPABASE_URL || !SUPABASE_SERVICE_ROLE_KEY) {
  console.error("[products-report] Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY");
}

const supabase = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, {
  global: {
    headers: { "X-Client-Info": "trenddrop-products-report" },
  },
});

function makeCsv(rows: any[], headers: string[]): string {
  const headerLine = headers.join(",");

  const bodyLines = rows.map((row) =>
    headers
      .map((key) => {
        const value = row[key];
        if (value === null || value === undefined) return "";
        const s = String(value);
        // Escape quotes
        return `"${s.replace(/"/g, '""')}"`
      })
      .join(",")
  );

  return [headerLine, ...bodyLines].join("\n");
}

serve(async (req) => {
  const url = new URL(req.url);

  const type = url.searchParams.get("type") ?? "recent"; // e.g. "recent"
  const days = Number(url.searchParams.get("days") ?? "1");
  const limit = Number(url.searchParams.get("limit") ?? "100");
  const format = url.searchParams.get("format") ?? "json"; // "json" | "csv"
  const provider = url.searchParams.get("provider") ?? url.searchParams.get("source") ?? undefined;

  try {
    // Base query: use your cleaned products view
    let query = supabase.from("v_products_clean").select("*");

    if (type === "recent" && days > 0) {
      const since = new Date();
      since.setDate(since.getDate() - days);
      query = query.gte("inserted_at", since.toISOString());
    }

    if (provider) {
      query = query.eq("provider", provider);
    }

    query = query
      .order("inserted_at", { ascending: false })
      .limit(isNaN(limit) ? 100 : limit);

    const { data, error } = await query;

    if (error) {
      console.error("[products-report] Supabase query error:", error);
      throw error;
    }

    if (!data) {
      return new Response(
        JSON.stringify({ ok: true, data: [] }),
        {
          status: 200,
          headers: {
            "Content-Type": "application/json; charset=utf-8",
            "Access-Control-Allow-Origin": "*",
          },
        },
      );
    }

    if (format === "csv") {
      // Columns we care about in CSV
      const headers = [
        "id",
        "provider",
        "source",
        "title",
        "price",
        "currency",
        "seller_feedback",
        "signals",
        "top_rated",
        "image_url",
        "url",
        "inserted_at",
      ];

      const csv = makeCsv(data, headers);

      return new Response(csv, {
        status: 200,
        headers: {
          "Content-Type": "text/csv; charset=utf-8",
          "Access-Control-Allow-Origin": "*",
        },
      });
    }

    // Default: JSON
    return new Response(
      JSON.stringify({ ok: true, data }),
      {
        status: 200,
        headers: {
          "Content-Type": "application/json; charset=utf-8",
          "Access-Control-Allow-Origin": "*",
        },
      },
    );
  } catch (err) {
    console.error("[products-report] Handler error:", err);
    const message = err instanceof Error ? err.message : String(err);

    return new Response(
      JSON.stringify({ ok: false, error: message }),
      {
        status: 500,
        headers: {
          "Content-Type": "application/json; charset=utf-8",
          "Access-Control-Allow-Origin": "*",
        },
      },
    );
  }
});
