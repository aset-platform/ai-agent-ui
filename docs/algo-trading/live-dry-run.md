# Live Dry-Run Mode

Dry-run mode lets you rehearse the full V2-5 live-orders pipeline
against synthetic Kite responses **before any real-money exposure**.
All safety gates, risk checks, caps validation, and event emission
run normally. The only thing that changes is the final Kite REST call
— it never happens.

---

## Why this exists

Enabling live trading for the first time is a high-stakes operation.
Dry-run mode gives you confidence that:

- Your strategy evaluates correctly against live tick data.
- Pre-trade risk checks (`pre_trade_check`) behave as expected.
- The kill-switch cancellation path works under your caps.
- Events (`order_submitted_live`, `order_filled_live`) are emitted
  with correct shape and timestamps.
- The frontend banner, gate toggle, and in-flight order list update
  correctly.

**What dry-run does NOT test:**

- Real Kite REST connectivity or authentication (no HTTP call is made).
- Order acknowledgement latency from the Kite exchange gateway.
- Fill price slippage (dry-run fills at `last_price`).
- Reject responses from Kite (e.g., insufficient funds, freeze).

---

## How to enable

1. Add or update the environment variable in your `.env`:

   ```env
   ALGO_LIVE_DRY_RUN=true
   ```

2. Recreate the backend container so it picks up the new env var:

   ```bash
   docker compose up -d --force-recreate backend
   ```

3. The frontend will display an amber **DRY RUN MODE** banner at
   the top of the Live section once the status endpoint responds.

Accepted truthy values: `true`, `1`, `yes` (case-insensitive).

---

## Step-by-step rehearsal

### 1. Enable dry-run and restart

```bash
# .env
ALGO_LIVE_DRY_RUN=true

docker compose up -d --force-recreate backend
```

### 2. Set caps

In the **Safety belts** panel, set `max_inr`, `max_orders_per_day`,
and `allowed_tickers` for the strategy under test.

### 3. Disarm the kill switch

Navigate to **Kill Switch** and ensure it is disarmed (green).

### 4. Enable live mode

Click **Enable live orders** in the 4-gate toggle.  All gates must
be green.  The frontend will show the amber **DRY RUN MODE** banner
prominently — confirm it is visible.

### 5. Start a live run

Launch the live WebSocket run via the Active Runs panel or CLI.
Watch the backend logs:

```
[DRY_RUN] place_order symbol=RELIANCE side=BUY qty=5
    type=MARKET limit_price=0.0 product=CNC variety=regular
    -> DRY_a3f9c1e02b4d
[DRY_RUN] synthetic fill: symbol=RELIANCE side=BUY qty=5
    price=2541.30 fees=12.43 kite_order_id=DRY_a3f9c1e02b4d
```

Each `DRY_` order id is unique.  Synthetic fills arrive ~100 ms
after submission.

### 6. Arm the kill switch mid-run

Toggle the kill switch **ON** while the run is active.  Verify:

- The **Kill switch ARMED** chip appears in the Paper & Live tab.
- `cancel_in_flight_orders` is called.
- Logs show `[DRY_RUN] cancel_order kite_order_id=DRY_...`.
- The in-flight orders list clears.

### 7. Disarm and run again

Disarm the kill switch and start a second run to confirm the cancel
path was idempotent and positions reset correctly.

---

## How to disable (switch to real orders)

1. Edit `.env`:

   ```env
   ALGO_LIVE_DRY_RUN=false
   # or remove the line entirely
   ```

2. Recreate the backend:

   ```bash
   docker compose up -d --force-recreate backend
   ```

3. Confirm the amber banner is gone from the frontend before placing
   any order.

---

## Event payload differences

In dry-run mode, `order_submitted_live` and `order_filled_live`
events include an extra field:

```json
{
  "dry_run": true
}
```

This field is absent (or `false`) on real-order events, so you can
filter event history by mode if needed.

---

## Architecture notes

- The dry-run flag is read once at `KiteClient.__init__` from
  `ALGO_LIVE_DRY_RUN` env var. An explicit `dry_run=` kwarg
  overrides the env var (used in tests).
- The short-circuit happens **after** parameter validation inside
  `place_order` / `cancel_order` / `modify_order` — invalid
  `order_type` / `product` / `variety` values still raise
  `ValueError` in dry-run mode (this is intentional: the real path
  would also reject them).
- Synthetic fills use the real `IndianFeeModel` with today's rate
  sheet, so the fee amounts are representative.
- `GET /v1/algo/live/status/{strategy_id}` always returns
  `"dry_run": true/false` — the frontend polls this every 15 s.
