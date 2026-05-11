import { redirect } from "next/navigation";

import { mapLegacyTab } from "./redirectMap";

export default async function AlgoTradingIndex({
  searchParams,
}: {
  searchParams: Promise<{ tab?: string }>;
}) {
  const params = await searchParams;
  redirect(mapLegacyTab(params.tab ?? null));
}
