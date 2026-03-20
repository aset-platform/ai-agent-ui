/**
 * Clientside callbacks for the AI Stock Analysis Dashboard.
 *
 * Dash's ``clientside_callback`` API requires functions registered under
 * ``window.dash_clientside.<namespace>.<function_name>``.
 */

if (!window.dash_clientside) {
  window.dash_clientside = {};
}

window.dash_clientside.clientside = {
  /**
   * Toggle dark mode on/off.
   *
   * On first load (n_clicks is undefined/null) the function reads the
   * ``?theme=dark`` query parameter (passed by the frontend iframe) or
   * falls back to the persisted ``theme-store`` value.
   *
   * Returns ``[storeValue, buttonEmoji]``.
   */
  toggleTheme: function (nClicks, currentTheme, search) {
    var theme = currentTheme || "light";

    if (!nClicks) {
      // First load — check URL query param from frontend iframe
      if (search) {
        var params = new URLSearchParams(search.replace(/^\?/, ""));
        var urlTheme = params.get("theme");
        if (urlTheme === "dark" || urlTheme === "light") {
          theme = urlTheme;
        }
      }
    } else {
      // Toggle on click
      theme = theme === "dark" ? "light" : "dark";
    }

    // Apply or remove dark-mode class on body
    if (theme === "dark") {
      document.body.classList.add("dark-mode");
    } else {
      document.body.classList.remove("dark-mode");
    }

    var emoji = theme === "dark" ? "\u2600\uFE0F" : "\uD83C\uDF19";
    return [theme, emoji];
  },
};
