/** App entry: styles, legacy SPA, accessibility. */
import "./tokens.css";
import "./styles.css";
import "./app";
import {
  enhanceBoardAccessibility,
  watchMovesForAnnouncements,
} from "./board/a11y";

function bootA11y() {
  enhanceBoardAccessibility();
  watchMovesForAnnouncements();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", bootA11y);
} else {
  bootA11y();
}
