import { redirect } from "next/navigation";

/**
 * Legacy insights route — redirects to the native
 * Next.js insights page at /analytics/insights.
 *
 * The old page embedded Dash via iframe; the native
 * replacement is feature-complete with all 7 tabs.
 */
export default function InsightsPage() {
  redirect("/analytics/insights");
}
