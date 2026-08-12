/**
 * PostHog product analytics — People, Insights, Retention, Session Replay.
 * Official SDK: https://posthog.com/docs/libraries/js
 *
 * Off unless the server exposes posthog_project_api_key via /api/auth/config.
 * Project API keys are public by design (same class as a GA4 measurement ID).
 */
import posthog from "posthog-js";

let initialized = false;
let optedOutAdmin = false;

export type AnalyticsUser = {
  id?: number | string;
  email?: string;
  display_name?: string;
  plan?: string;
  auth_provider?: string;
  is_pro?: boolean;
  is_trialing?: boolean;
  chesscom_username?: string;
  lichess_username?: string;
  can_view_analytics?: boolean;
  created_at?: number;
};

export type PosthogSiteConfig = {
  posthog_project_api_key?: string | null;
  posthog_host?: string | null;
};

function personProps(user: AnalyticsUser): Record<string, unknown> {
  const props: Record<string, unknown> = {
    email: user.email || undefined,
    name: (user.display_name || "").trim() || undefined,
    plan: user.plan || "free",
    auth_provider: user.auth_provider || undefined,
    is_pro: !!user.is_pro,
    is_trialing: !!user.is_trialing,
  };
  const cc = (user.chesscom_username || "").trim();
  const li = (user.lichess_username || "").trim();
  if (cc) props.chesscom_username = cc;
  if (li) props.lichess_username = li;
  if (user.created_at) {
    props.signup_at = new Date(user.created_at * 1000).toISOString();
  }
  return props;
}

/** Init once after site config is available. Safe to call repeatedly. */
export function initProductAnalytics(
  cfg: PosthogSiteConfig,
  getUser: () => AnalyticsUser | null | undefined
): void {
  const key = (cfg.posthog_project_api_key || "").trim();
  if (!key || initialized) return;

  const host = (cfg.posthog_host || "").trim() || "https://us.i.posthog.com";
  posthog.init(key, {
    api_host: host,
    // Pin SDK defaults snapshot (PostHog recommended).
    defaults: "2026-05-30",
    person_profiles: "identified_only",
    capture_pageview: true,
    capture_pageleave: true,
    persistence: "localStorage+cookie",
    loaded: (ph) => {
      const user = getUser();
      if (user?.id) identifyProductUser(user);
      else if (user?.can_view_analytics) {
        ph.opt_out_capturing();
        optedOutAdmin = true;
      }
    },
  });
  initialized = true;
}

/** Link anonymous browsing → signed-in person (email on the People profile). */
export function identifyProductUser(user: AnalyticsUser | null | undefined): void {
  if (!initialized || !user?.id) return;

  // Keep the site owner out of product metrics (same idea as GA exclusion).
  if (user.can_view_analytics) {
    if (!optedOutAdmin) {
      posthog.opt_out_capturing();
      optedOutAdmin = true;
    }
    return;
  }

  if (optedOutAdmin) {
    posthog.opt_in_capturing();
    optedOutAdmin = false;
  }

  posthog.identify(String(user.id), personProps(user));
}

/** Call on logout so the next browser user is not merged into this person. */
export function resetProductAnalytics(): void {
  if (!initialized) return;
  optedOutAdmin = false;
  posthog.reset();
}

export function trackProductEvent(
  event: string,
  properties?: Record<string, unknown>
): void {
  if (!initialized || optedOutAdmin) return;
  try {
    posthog.capture(event, properties);
  } catch {
    /* never block UX on analytics */
  }
}

export function isProductAnalyticsReady(): boolean {
  return initialized;
}
