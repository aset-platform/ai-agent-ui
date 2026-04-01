import { redirect } from "next/navigation";

/**
 * Marketplace functionality has been merged into the
 * unified Analytics page. Redirect for backward compat.
 */
export default function MarketplacePage() {
  redirect("/analytics");
}
