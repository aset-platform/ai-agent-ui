"use client";

import { ConnectBrokerTab } from "@/components/algo-trading/ConnectBrokerTab";

export default function BrokerClient() {
  return (
    <div className="space-y-4 p-6" data-testid="algo-broker-page">
      <h1 className="text-xl font-semibold">Zerodha Connect</h1>
      <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-4">
        <ConnectBrokerTab />
      </div>
    </div>
  );
}
