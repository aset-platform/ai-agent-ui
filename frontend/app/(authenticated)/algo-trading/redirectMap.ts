/** Map legacy ?tab= IDs (pre-2026-05-11) to their new homes. */
export function mapLegacyTab(tab: string | null): string {
  if (tab === "connect") return "/algo-trading/broker";
  const strategiesTabs = [
    "instruments",
    "strategies",
    "backtest",
    "paper",
    "performance",
    "replay",
    "settings",
  ];
  if (tab && strategiesTabs.includes(tab)) {
    return `/algo-trading/strategies?tab=${tab}`;
  }
  return "/algo-trading/strategies";
}
