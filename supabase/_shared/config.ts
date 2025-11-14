// supabase/_shared/config.ts
// Centralized configuration for Deno Edge Functions (TrendDrop)
// Only Deno-compatible APIs. No Node/process imports.
import { createClient as createSupabaseClient } from "jsr:@supabase/supabase-js@2.46.1";

type Mode = "live" | "test";

const mode: Mode = (Deno.env.get("MODE") ?? "live").toLowerCase() === "test" ? "test" : "live";
const isLive = mode === "live";

function read(name: string): string | null {
  const v = Deno.env.get(name);
  if (typeof v !== "string") return null;
  const trimmed = v.trim();
  return trimmed.length ? trimmed : null;
}

function requireEnv(name: string): string {
  const v = read(name);
  if (v) return v;
  const msg = `[config] missing required env ${name}`;
  if (isLive) {
    // Production: log error but do not throw to avoid crash loops.
    console.error(msg);
    return "";
  } else {
    // Dev/Test: fail fast so it is obvious during development.
    throw new Error(msg);
  }
}

// Stripe
const STRIPE_SECRET_KEY_LIVE = read("STRIPE_SECRET_KEY_LIVE");
const STRIPE_SECRET_KEY_TEST = read("STRIPE_SECRET_KEY_TEST");
const STRIPE_SECRET_KEY_FALLBACK = read("STRIPE_SECRET_KEY"); // optional single-key setups
const stripeSecretKey =
  (isLive ? STRIPE_SECRET_KEY_LIVE : STRIPE_SECRET_KEY_TEST) ??
  STRIPE_SECRET_KEY_FALLBACK ??
  null;

const STRIPE_WEBHOOK_SECRET_LIVE = read("STRIPE_WEBHOOK_SECRET_LIVE");
const STRIPE_WEBHOOK_SECRET_TEST = read("STRIPE_WEBHOOK_SECRET_TEST");
const STRIPE_WEBHOOK_SECRET_FALLBACK = read("STRIPE_WEBHOOK_SECRET");
const stripeWebhookSecret =
  (isLive ? STRIPE_WEBHOOK_SECRET_LIVE : STRIPE_WEBHOOK_SECRET_TEST) ??
  STRIPE_WEBHOOK_SECRET_FALLBACK ??
  null;

// Brevo
const brevoApiKey = read("BREVO_API_KEY");
const emailFrom = read("EMAIL_FROM");

// Supabase
const supabaseUrl = read("SUPABASE_URL") ?? (isLive ? "" : requireEnv("SUPABASE_URL"));
const supabaseServiceRoleKey =
  read("SUPABASE_SERVICE_ROLE_KEY") ?? (isLive ? "" : requireEnv("SUPABASE_SERVICE_ROLE_KEY"));
const supabaseAnonKey = read("SUPABASE_ANON_KEY");

// Telegram
const telegramInviteUrl = read("TELEGRAM_INVITE_URL");
const telegramBotToken = read("TELEGRAM_BOT_TOKEN");
const telegramCommunityChatId = read("TELEGRAM_COMMUNITY_CHAT_ID");
const telegramAlertChatId = read("TELEGRAM_ALERT_CHAT_ID") ?? read("TELEGRAM_CHAT_ID");

// Misc
const reportsBucket =
  read("REPORTS_BUCKET") ?? read("SUPABASE_BUCKET") ?? "trenddrop-reports";
const requireGumroadAuth = String(read("REQUIRE_GUMROAD_AUTH") || "").toLowerCase() === "true";
const gumroadWebhookSecret = read("GUMROAD_WEBHOOK_SECRET");
const gumroadSellerId = read("GUMROAD_SELLER_ID");
const payhipApiKey = read("PAYHIP_API_KEY");

// Create a single service client once per module load
const db = (supabaseUrl && supabaseServiceRoleKey)
  ? createSupabaseClient(supabaseUrl, supabaseServiceRoleKey, { auth: { persistSession: false } })
  : null;
if (!db) console.error("[config] Supabase client not initialized (missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY)");

export {
  mode,
  isLive,
  // Stripe
  stripeSecretKey,
  stripeWebhookSecret,
  // Brevo / email
  brevoApiKey,
  emailFrom,
  // Supabase
  supabaseUrl,
  supabaseServiceRoleKey,
  supabaseAnonKey,
  db,
  // Telegram
  telegramInviteUrl,
  telegramBotToken,
  telegramCommunityChatId,
  telegramAlertChatId,
  // Misc
  reportsBucket,
  requireGumroadAuth,
  gumroadWebhookSecret,
  gumroadSellerId,
  payhipApiKey,
};


