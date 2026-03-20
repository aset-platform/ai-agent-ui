declare module "plotly.js-basic-dist" {
  import Plotly from "plotly.js";
  export = Plotly;
}

declare module "react-plotly.js/factory" {
  import type { PlotParams } from "react-plotly.js";
  import type { ComponentType } from "react";
  function createPlotlyComponent(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    plotly: any,
  ): ComponentType<PlotParams>;
  export default createPlotlyComponent;
}
