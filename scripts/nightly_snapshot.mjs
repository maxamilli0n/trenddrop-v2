// scripts/nightly_snapshot.mjs
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import fetch from "node-fetch";
import { createClient } from "@supabase/supabase-js";

const {
  SUPABASE_URL,
  SUPABASE_SERVICE_ROLE_KEY,
  SUPABASE_PROJECT_REF,
  SUPABASE_ANON_KEY,
  REPORTS_BUCKET = "trenddrop-reports",
  TELEGRAM_BOT_TOKEN,
  TELEGRAM_ADMIN_CHAT_ID,
  TELEGRAM_CHAT_ID, // legacy fallback
} = process.env;

const ADMIN_CHAT = TELEGRAM_ADMIN_CHAT_ID || TELEGRAM_CHAT_ID;

if (!SUPABASE_URL || !SUPABASE_SERVICE_ROLE_KEY || !SUPABASE_PROJECT_REF || !SUPABASE_ANON_KEY) {
  console.error(
    "Missing required env vars. Need SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_PROJECT_REF, SUPABASE_ANON_KEY."
  );
  process.exit(1);
}

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const outDir = path.join(__dirname, "..", "snapshots");

// 1) Pull CSV from private products-report function
const today = new Date();
const yyyy = today.getUTCFullYear();
const mm = String(today.getUTCMonth() + 1).padStart(2, "0");
const dd = String(today.getUTCDate()).padStart(2, "0");
const stamp = `${yyyy}-${mm}-${dd}`;

const limit = 1000;
const days = 7;
const type = "recent";
const url = `https://${SUPABASE_PROJECT_REF}.functions.supabase.co/products-report?type=${type}&days=${days}&format=csv&limit=${limit}`;

const headers = { Authorization: `Bearer ${SUPABASE_ANON_KEY}` };

console.log(`[snapshot] Fetching CSV from ${url}`);
const res = await fetch(url, { headers });
if (!res.ok) {
  const body = await res.text().catch(() => "");
  throw new Error(`[snapshot] products-report failed ${res.status}: ${body}`);
}
const csv = await res.text();

// 2) Save locally (artifact in Actions run)
await fs.mkdir(outDir, { recursive: true });
const localName = `products-${type}-${stamp}.csv`;
const localPath = path.join(outDir, localName);
await fs.writeFile(localPath, csv, "utf8");
console.log(`[snapshot] Saved -> ${localPath} (${csv.length} bytes)`);

// 3) Upload to Supabase Storage
const supa = createClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, { auth: { persistSession: false } });

try {
  await supa.storage.createBucket(REPORTS_BUCKET, { public: false });
  console.log(`[snapshot] Created bucket '${REPORTS_BUCKET}'`);
} catch (e) {
  console.log(`[snapshot] Bucket '${REPORTS_BUCKET}' already exists or create failed: ${e?.message ?? e}`);
}

const storagePath = `snapshots/${yyyy}/${mm}/products-${type}-${stamp}.csv`;
const upload = await supa.storage.from(REPORTS_BUCKET).upload(
  storagePath,
  new Blob([csv], { type: "text/csv" }),
  { upsert: true, contentType: "text/csv" }
);
if (upload.error) throw upload.error;
console.log(`[snapshot] Uploaded to storage -> ${REPORTS_BUCKET}/${storagePath}`);

// 4) Signed URL (30 days)
const { data: sig, error: sigErr } = await supa.storage.from(REPORTS_BUCKET).createSignedUrl(storagePath, 60 * 60 * 24 * 30);
if (sigErr) throw sigErr;
const signedUrl = sig.signedUrl;
console.log(`[snapshot] Signed URL -> ${signedUrl}`);

// 5) Telegram ping — ADMIN ONLY
if (TELEGRAM_BOT_TOKEN && ADMIN_CHAT) {
  const text =
    `📊 Nightly TrendDrop snapshot (${stamp})\n` +
    `• Window: last ${days} days\n` +
    `• Rows: up to ${limit}\n` +
    `• Download CSV: ${signedUrl}`;

  const tgRes = await fetch(`https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ chat_id: ADMIN_CHAT, text }),
  });

  if (!tgRes.ok) {
    const b = await tgRes.text().catch(() => "");
    console.warn(`[snapshot] Telegram failed ${tgRes.status}: ${b}`);
  } else {
    console.log(`[snapshot] Telegram pinged admin ✅`);
  }
} else {
  console.log(`[snapshot] Telegram disabled (missing bot token or admin chat id)`); 
}

console.log("[snapshot] Done.");
