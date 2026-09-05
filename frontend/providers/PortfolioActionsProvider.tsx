"use client";
/**
 * Portfolio Actions — cross-page provider.
 *
 * Hosts the Add/Edit/Delete portfolio modals once at
 * the authenticated-layout level so any page (Dashboard,
 * Analysis → Recommendations, chat, etc.) can open them
 * in place instead of route-hopping to /dashboard.
 *
 * Consumers call ``usePortfolioActions()`` and invoke
 * ``openAdd(ticker?)``, ``openEdit(ticker)``, or
 * ``openDelete(ticker)``.  The backend already auto-
 * marks matching recommendations as ``acted_on`` when
 * a transaction hits — no extra plumbing here.
 */

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
} from "react";
import { useSWRConfig } from "swr";
import {
  usePortfolio,
  type PortfolioHolding,
} from "@/hooks/usePortfolio";
import { useRegistry } from "@/hooks/useDashboardData";
import { API_URL } from "@/lib/config";
import { AddStockModal } from "@/components/widgets/AddStockModal";
import { EditStockModal } from "@/components/widgets/EditStockModal";
import ClosePositionModal from "@/components/widgets/ClosePositionModal";
import { PortfolioTransactionsModal } from "@/components/widgets/PortfolioTransactionsModal";
import { ConfirmDialog } from "@/components/ConfirmDialog";

interface PortfolioActionsCtx {
  openAdd: (ticker?: string) => void;
  openEdit: (ticker: string) => void;
  openDelete: (ticker: string) => void;
  openTransactions: (ticker: string) => void;
  openClose: (ticker: string) => void;
}

const Ctx =
  createContext<PortfolioActionsCtx | null>(null);

export function usePortfolioActions():
  PortfolioActionsCtx {
  const v = useContext(Ctx);
  if (!v) {
    // Graceful no-op when rendered outside the
    // provider (e.g. public pages) so callers don't
    // crash — buttons simply won't do anything.
    return {
      openAdd: () => {},
      openEdit: () => {},
      openDelete: () => {},
      openTransactions: () => {},
      openClose: () => {},
    };
  }
  return v;
}

export function PortfolioActionsProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const portfolio = usePortfolio();
  const registry = useRegistry();
  const { mutate } = useSWRConfig();

  const registryTickers = useMemo(
    () =>
      registry.value?.tickers?.map((t) => t.ticker) ??
      [],
    [registry.value],
  );

  const [showAdd, setShowAdd] = useState(false);
  const [addSeed, setAddSeed] = useState<
    string | undefined
  >(undefined);
  const [editingTicker, setEditingTicker] = useState<
    string | null
  >(null);
  const [deleteConfirm, setDeleteConfirm] = useState<{
    ticker: string;
    txnId: string;
  } | null>(null);
  const [txnTicker, setTxnTicker] = useState<
    string | null
  >(null);
  const [closeTarget, setCloseTarget] = useState<
    string | null
  >(null);

  const openAdd = useCallback(
    (ticker?: string) => {
      setAddSeed(ticker);
      setShowAdd(true);
    },
    [],
  );
  const openEdit = useCallback(
    (ticker: string) => setEditingTicker(ticker),
    [],
  );
  const openDelete = useCallback(
    (ticker: string) => {
      const h: PortfolioHolding | undefined =
        portfolio.holdings.find(
          (x) => x.ticker === ticker,
        );
      if (h?.transaction_id) {
        setDeleteConfirm({
          ticker,
          txnId: h.transaction_id,
        });
      }
    },
    [portfolio.holdings],
  );

  const openTransactions = useCallback(
    (ticker: string) => setTxnTicker(ticker),
    [],
  );

  const openClose = useCallback(
    (ticker: string) => setCloseTarget(ticker),
    [],
  );

  const ctxValue = useMemo<PortfolioActionsCtx>(
    () => ({
      openAdd,
      openEdit,
      openDelete,
      openTransactions,
      openClose,
    }),
    [
      openAdd,
      openEdit,
      openDelete,
      openTransactions,
      openClose,
    ],
  );

  // Lookup existing holding for the Edit modal
  const editHolding = useMemo(
    () =>
      editingTicker
        ? portfolio.holdings.find(
            (h) => h.ticker === editingTicker,
          ) ?? null
        : null,
    [portfolio.holdings, editingTicker],
  );

  // Lookup existing holding for the Close modal
  const closeHoldingTarget = useMemo(
    () =>
      closeTarget
        ? portfolio.holdings.find(
            (h) => h.ticker === closeTarget,
          ) ?? null
        : null,
    [portfolio.holdings, closeTarget],
  );

  return (
    <Ctx.Provider value={ctxValue}>
      {children}

      <AddStockModal
        isOpen={showAdd}
        tickers={registryTickers}
        onClose={() => {
          setShowAdd(false);
          setAddSeed(undefined);
        }}
        onAdd={async (data) => {
          await portfolio.addHolding(data);
        }}
        initialTicker={addSeed}
      />

      {editHolding && (
        <EditStockModal
          isOpen={editingTicker !== null}
          ticker={editHolding.ticker}
          currentQty={editHolding.quantity}
          currentPrice={editHolding.avg_price}
          onClose={() => setEditingTicker(null)}
          onSave={async (data) => {
            if (editHolding.transaction_id) {
              await portfolio.editHolding(
                editHolding.transaction_id,
                data,
              );
            }
          }}
        />
      )}

      {closeHoldingTarget && (
        <ClosePositionModal
          isOpen={closeTarget !== null}
          holding={{
            ticker: closeHoldingTarget.ticker,
            quantity: closeHoldingTarget.quantity,
            avg_price: closeHoldingTarget.avg_price,
            currency: closeHoldingTarget.currency,
          }}
          onClose={() => setCloseTarget(null)}
          onConfirm={async (body) => {
            await portfolio.closeHolding(
              closeTarget!,
              body,
            );
            setCloseTarget(null);
            mutate(
              `${API_URL}/users/me/portfolio/closed`,
            );
          }}
        />
      )}

      <PortfolioTransactionsModal
        isOpen={txnTicker !== null}
        ticker={txnTicker}
        onClose={() => setTxnTicker(null)}
      />

      <ConfirmDialog
        open={deleteConfirm !== null}
        title="Remove Stock"
        message={
          deleteConfirm
            ? `Remove ${deleteConfirm.ticker} from your portfolio? This cannot be undone.`
            : ""
        }
        confirmLabel="Remove"
        variant="danger"
        onConfirm={() => {
          if (deleteConfirm) {
            portfolio.deleteHolding(
              deleteConfirm.txnId,
            );
          }
          setDeleteConfirm(null);
        }}
        onCancel={() => setDeleteConfirm(null)}
      />
    </Ctx.Provider>
  );
}
