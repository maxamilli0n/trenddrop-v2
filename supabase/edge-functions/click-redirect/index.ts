// deno-lint-ignore-file no-explicit-any
import { serve } from "https://deno.land/std@0.224.0/http/server.ts";
import { db } from "../../functions/_shared/config.ts";

const supabase = db;

serve(async (req) => {
  try{
    if (!supabase) return new Response("supabase not configured", { status: 500 });
    const urlObj = new URL(req.url);
    const target = urlObj.searchParams.get("url");
    if(!target){
      return new Response("missing url", { status: 400 });
    }
    await supabase.from("clicks").insert({ product_url: target });
    return Response.redirect(target, 302);
  }catch(e){
    return new Response("error", { status: 500 });
  }
});


