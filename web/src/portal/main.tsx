import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/fraunces/soft.css";
import "@fontsource-variable/inter/index.css";
import { startSentry } from "../sentry";
import "../styles.css";
import { Portal } from "./Portal";

startSentry();

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <Portal />
    </StrictMode>,
  );
}
