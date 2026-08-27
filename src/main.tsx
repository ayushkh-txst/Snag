import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles/tokens.css";
import "./styles/base.css";
import "./styles/app.css";
import "./styles/screens.css";

try {
  const saved = localStorage.getItem("snag-theme");
  if (saved === "dark" || saved === "light") document.documentElement.dataset.theme = saved;
} catch { /* private mode — fall through to system */ }

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
