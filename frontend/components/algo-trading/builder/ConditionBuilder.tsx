"use client";

import { useMemo } from "react";

import { STRATEGY_FEATURES } from "../strategyFeatureCatalog";
import {
  astRootToVisualSpec,
  makeDefaultRow,
  visualSpecToAstRoot,
  type ConditionGroup,
  type ConditionOp,
  type ConditionRow,
  type EntryAction,
  type ExitAction,
  type VisualSpec,
} from "./visualStrategy";

// ─── Feature groups for <optgroup> dropdowns ────────────────────

const SOURCE_LABELS: Record<string, string> = {
  ohlcv: "Price & Volume",
  technical: "Technical",
  fundamentals: "Fundamentals",
  recommendation: "Recommendation",
  forecast: "Forecast",
  regime: "Regime & Market",
  factor: "Factor Library",
  intraday_feature_store: "Intraday",
};

const FEATURE_GROUPS = Object.entries(
  STRATEGY_FEATURES.reduce<Record<string, typeof STRATEGY_FEATURES>>(
    (acc, f) => {
      (acc[f.source] ??= []).push(f);
      return acc;
    },
    {},
  ),
).map(([source, features]) => ({
  source,
  label: SOURCE_LABELS[source] ?? source,
  features,
}));

const NUMERIC_OPS: { value: ConditionOp; label: string }[] = [
  { value: ">", label: ">" },
  { value: ">=", label: ">=" },
  { value: "==", label: "==" },
  { value: "!=", label: "!=" },
  { value: "<=", label: "<=" },
  { value: "<", label: "<" },
];
const STRING_OPS: { value: ConditionOp; label: string }[] = [
  { value: "==", label: "is" },
  { value: "!=", label: "is not" },
];

const SEL =
  "text-xs rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 px-1.5 py-1";

// ─── Root component ─────────────────────────────────────────────

interface Props {
  root: unknown;
  onChangeRoot: (root: unknown) => void;
}

export function ConditionBuilder({ root, onChangeRoot }: Props) {
  const spec = useMemo(() => astRootToVisualSpec(root), [root]);

  if (!spec) {
    return (
      <div className="text-xs text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20 rounded p-2">
        This strategy root is too complex for the visual editor — use AST
        Tree or the JSON pane instead.
      </div>
    );
  }

  function update(patch: Partial<VisualSpec>) {
    const merged = { ...spec!, ...patch };
    onChangeRoot(visualSpecToAstRoot(merged));
  }

  return (
    <div className="space-y-3" data-testid="algo-condition-builder">
      <ConditionSection
        title="Entry Conditions"
        hint="enter when"
        group={spec.entryGroup}
        data-testid="algo-cond-entry"
        onChange={(g) => update({ entryGroup: g })}
      />

      <EntryActionEditor
        action={spec.entryAction}
        onChange={(a) => update({ entryAction: a })}
      />

      <label className="flex items-center gap-2 text-xs cursor-pointer select-none text-gray-600 dark:text-gray-400">
        <input
          type="checkbox"
          checked={spec.hasExitCondition}
          onChange={(e) => update({ hasExitCondition: e.target.checked })}
          data-testid="algo-cond-has-exit"
          className="rounded"
        />
        Define separate exit conditions
        <span className="text-gray-400">
          (otherwise exits whenever entry is false)
        </span>
      </label>

      {spec.hasExitCondition && (
        <ConditionSection
          title="Exit Conditions"
          hint="exit when"
          group={spec.exitGroup}
          data-testid="algo-cond-exit"
          onChange={(g) => update({ exitGroup: g })}
        />
      )}

      <ExitActionEditor
        label={
          spec.hasExitCondition
            ? "Exit action (when exit condition is met)"
            : "Exit action (when entry is not met)"
        }
        action={spec.exitAction}
        testid="algo-cond-exit-action"
        onChange={(a) => update({ exitAction: a })}
      />

      {spec.hasExitCondition && (
        <ExitActionEditor
          label="Fallback (when exit condition is NOT met)"
          action={spec.fallbackAction}
          testid="algo-cond-fallback-action"
          onChange={(a) => update({ fallbackAction: a })}
        />
      )}
    </div>
  );
}

// ─── Condition section ──────────────────────────────────────────

function ConditionSection({
  title,
  hint,
  group,
  onChange,
  "data-testid": testid,
}: {
  title: string;
  hint: string;
  group: ConditionGroup;
  onChange: (g: ConditionGroup) => void;
  "data-testid"?: string;
}) {
  function updateRow(i: number, r: ConditionRow) {
    const rows = [...group.rows];
    rows[i] = r;
    onChange({ ...group, rows });
  }
  function removeRow(i: number) {
    onChange({ ...group, rows: group.rows.filter((_, idx) => idx !== i) });
  }
  function addRow() {
    onChange({ ...group, rows: [...group.rows, makeDefaultRow()] });
  }

  return (
    <div
      data-testid={testid}
      className="rounded border border-gray-200 dark:border-gray-700 p-3 space-y-2"
    >
      <div className="flex items-center gap-2">
        <span className="text-xs font-semibold text-gray-700 dark:text-gray-200">
          {title}
        </span>
        <span className="text-xs text-gray-400">{hint}</span>
        <div className="ml-auto flex items-center gap-1.5">
          <span className="text-xs text-gray-500">Match:</span>
          <select
            value={group.combinator}
            onChange={(e) =>
              onChange({
                ...group,
                combinator: e.target.value as "and" | "or",
              })
            }
            data-testid={testid ? `${testid}-combinator` : undefined}
            className={SEL}
          >
            <option value="and">ALL (AND)</option>
            <option value="or">ANY (OR)</option>
          </select>
        </div>
      </div>

      {group.rows.length === 0 && (
        <p className="text-xs text-gray-400 italic">
          No conditions — add one below.
        </p>
      )}

      <div className="space-y-1">
        {group.rows.map((row, i) => (
          <ConditionRowEditor
            key={row.id}
            row={row}
            onChange={(r) => updateRow(i, r)}
            onRemove={() => removeRow(i)}
          />
        ))}
      </div>

      <button
        type="button"
        onClick={addRow}
        data-testid={testid ? `${testid}-add` : undefined}
        className="text-xs text-indigo-600 dark:text-indigo-400 hover:underline"
      >
        + Add condition
      </button>
    </div>
  );
}

// ─── Single condition row ───────────────────────────────────────

function ConditionRowEditor({
  row,
  onChange,
  onRemove,
}: {
  row: ConditionRow;
  onChange: (r: ConditionRow) => void;
  onRemove: () => void;
}) {
  const feat = STRATEGY_FEATURES.find((f) => f.key === row.leftFeature);
  const isString = feat?.type === "string";
  const ops = isString ? STRING_OPS : NUMERIC_OPS;

  function handleFeatureChange(key: string) {
    const f = STRATEGY_FEATURES.find((x) => x.key === key);
    const newString = f?.type === "string";
    onChange({
      ...row,
      leftFeature: key,
      op: newString ? "==" : ">",
      rightType: "literal",
      rightLiteral: newString
        ? key === "regime_label"
          ? "BULL"
          : key === "time_of_day_bucket"
            ? "morning"
            : ""
        : "0",
      rightFeature: "",
    });
  }

  return (
    <div className="flex items-center gap-1.5 flex-wrap">
      {/* Left feature */}
      <select
        value={row.leftFeature}
        onChange={(e) => handleFeatureChange(e.target.value)}
        className={`${SEL} max-w-[200px]`}
      >
        {FEATURE_GROUPS.map((grp) => (
          <optgroup key={grp.source} label={grp.label}>
            {grp.features.map((f) => (
              <option key={f.key} value={f.key}>
                {f.label}
              </option>
            ))}
          </optgroup>
        ))}
      </select>

      {/* Operator */}
      <select
        value={row.op}
        onChange={(e) => onChange({ ...row, op: e.target.value as ConditionOp })}
        className={`${SEL} w-16`}
      >
        {ops.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>

      {/* Right value-type toggle (numeric only) */}
      {!isString && (
        <select
          value={row.rightType}
          onChange={(e) =>
            onChange({
              ...row,
              rightType: e.target.value as "literal" | "feature",
              rightLiteral: "0",
              rightFeature: STRATEGY_FEATURES.find((f) => f.type !== "string")?.key ?? "",
            })
          }
          className={`${SEL} w-20`}
        >
          <option value="literal">Value</option>
          <option value="feature">Feature</option>
        </select>
      )}

      {/* Right operand */}
      {isString ? (
        <StringValueInput
          featureKey={row.leftFeature}
          value={row.rightLiteral}
          onChange={(v) => onChange({ ...row, rightLiteral: v })}
        />
      ) : row.rightType === "feature" ? (
        <select
          value={row.rightFeature}
          onChange={(e) => onChange({ ...row, rightFeature: e.target.value })}
          className={`${SEL} max-w-[200px]`}
        >
          {FEATURE_GROUPS.map((grp) => (
            <optgroup key={grp.source} label={grp.label}>
              {grp.features
                .filter((f) => f.type !== "string")
                .map((f) => (
                  <option key={f.key} value={f.key}>
                    {f.label}
                  </option>
                ))}
            </optgroup>
          ))}
        </select>
      ) : (
        <input
          type="number"
          value={row.rightLiteral}
          onChange={(e) => onChange({ ...row, rightLiteral: e.target.value })}
          className={`${SEL} w-24`}
          step="any"
        />
      )}

      <button
        type="button"
        onClick={onRemove}
        className="text-xs text-red-500 hover:text-red-700 dark:hover:text-red-400 px-1 leading-none"
        aria-label="Remove condition"
      >
        ×
      </button>
    </div>
  );
}

function StringValueInput({
  featureKey,
  value,
  onChange,
}: {
  featureKey: string;
  value: string;
  onChange: (v: string) => void;
}) {
  if (featureKey === "regime_label") {
    return (
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={SEL}
      >
        {["BULL", "SIDEWAYS", "BEAR"].map((v) => (
          <option key={v} value={v}>
            {v}
          </option>
        ))}
      </select>
    );
  }
  if (featureKey === "time_of_day_bucket") {
    return (
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={SEL}
      >
        {["morning", "midday", "afternoon"].map((v) => (
          <option key={v} value={v}>
            {v}
          </option>
        ))}
      </select>
    );
  }
  return (
    <input
      type="text"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={`${SEL} w-24`}
      placeholder="value"
    />
  );
}

// ─── Entry action ───────────────────────────────────────────────

function EntryActionEditor({
  action,
  onChange,
}: {
  action: EntryAction;
  onChange: (a: EntryAction) => void;
}) {
  const weightPct = Math.round((parseFloat(action.weight) || 0) * 100);

  return (
    <div className="rounded border border-indigo-200 dark:border-indigo-800 p-3 bg-indigo-50/40 dark:bg-indigo-900/10">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs font-semibold text-gray-700 dark:text-gray-200">
          Entry action:
        </span>
        <select
          value={action.type}
          onChange={(e) =>
            onChange({ ...action, type: e.target.value as EntryAction["type"] })
          }
          data-testid="algo-cond-entry-action-type"
          className={SEL}
        >
          <option value="set_target_weight">Set target weight</option>
          <option value="buy">Buy (full position)</option>
        </select>

        {action.type === "set_target_weight" && (
          <>
            <input
              type="number"
              min="0.01"
              max="1"
              step="0.01"
              value={action.weight}
              onChange={(e) => onChange({ ...action, weight: e.target.value })}
              data-testid="algo-cond-entry-weight"
              className={`${SEL} w-20`}
              placeholder="0.10"
            />
            <span className="text-xs text-gray-400">
              ({weightPct}% of portfolio per stock)
            </span>
          </>
        )}
      </div>
    </div>
  );
}

// ─── Exit action ────────────────────────────────────────────────

function ExitActionEditor({
  label,
  action,
  onChange,
  testid,
}: {
  label: string;
  action: ExitAction;
  onChange: (a: ExitAction) => void;
  testid?: string;
}) {
  return (
    <div className="rounded border border-rose-200 dark:border-rose-800 p-3 bg-rose-50/40 dark:bg-rose-900/10">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs font-semibold text-gray-700 dark:text-gray-200">
          {label}:
        </span>
        <select
          value={action.type}
          onChange={(e) =>
            onChange({ ...action, type: e.target.value as ExitAction["type"] })
          }
          data-testid={testid ? `${testid}-type` : undefined}
          className={SEL}
        >
          <option value="exit">Exit position</option>
          <option value="hold">Hold</option>
        </select>

        {action.type === "exit" && (
          <select
            value={action.scope}
            onChange={(e) =>
              onChange({
                ...action,
                scope: e.target.value as ExitAction["scope"],
              })
            }
            data-testid={testid ? `${testid}-scope` : undefined}
            className={SEL}
          >
            <option value="this_symbol">This symbol</option>
            <option value="all_open">All open positions</option>
          </select>
        )}
      </div>
    </div>
  );
}
