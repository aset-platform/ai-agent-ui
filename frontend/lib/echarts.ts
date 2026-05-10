/**
 * Tree-shaken ECharts setup for portfolio analytics.
 *
 * Registers only pie, bar, and line chart modules
 * (~200 KB vs 800 KB for full ECharts).
 */

import * as echarts from "echarts/core";
import {
  PieChart,
  BarChart,
  LineChart,
  type PieSeriesOption,
  type BarSeriesOption,
  type LineSeriesOption,
} from "echarts/charts";
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  MarkAreaComponent,
  type GridComponentOption,
  type TooltipComponentOption,
  type LegendComponentOption,
  type MarkAreaComponentOption,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([
  PieChart,
  BarChart,
  LineChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  MarkAreaComponent,
  CanvasRenderer,
]);

export type EChartsOption = echarts.ComposeOption<
  | PieSeriesOption
  | BarSeriesOption
  | LineSeriesOption
  | GridComponentOption
  | TooltipComponentOption
  | LegendComponentOption
  | MarkAreaComponentOption
>;

export { echarts };
