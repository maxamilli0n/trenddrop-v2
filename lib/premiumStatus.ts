// lib/premiumStatus.ts

export interface PremiumMember {
  subscription_id: string;
  customer_id: string;
  email: string;
  status: string;
  plan_id: string | null;
  stripe_subscription_id: string | null;
  stripe_checkout_session_id: string | null;
  last_event_at: string | null;
  // if you add more columns to v_premium_members later,
  // stick them here too
}

export interface PremiumStatusResponse {
  isPremium: boolean;
  member: PremiumMember | null;
  error?: string;
}

const PROJECT_REF = "nkuanqodjejvojwvypvp"; // your Supabase project ref
const PREMIUM_STATUS_URL = `https://${PROJECT_REF}.functions.supabase.co/premium-status`;

/**
 * Check whether an email has an active premium membership.
 * Uses the Supabase Edge Function `premium-status`.
 */
export async function getPremiumStatus(
  email: string,
): Promise<PremiumStatusResponse> {
  if (!email.trim()) {
    return { isPremium: false, member: null, error: "empty_email" };
  }

  try {
    const res = await fetch(PREMIUM_STATUS_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: email.trim().toLowerCase() }),
    });

    if (!res.ok) {
      const text = await res.text().catch(() => "");
      return {
        isPremium: false,
        member: null,
        error: `http_${res.status}: ${text}`,
      };
    }

    const data = (await res.json()) as PremiumStatusResponse;
    // Normalize in case function ever omits fields
    return {
      isPremium: !!data.isPremium,
      member: data.member ?? null,
      error: data.error,
    };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    return { isPremium: false, member: null, error: msg };
  }
}

