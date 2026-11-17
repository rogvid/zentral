import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/fraunces/soft.css";
import "@fontsource-variable/inter/index.css";
import "../styles.css";
import { Portal } from "./Portal";

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <Portal />
    </StrictMode>,
  );
}
