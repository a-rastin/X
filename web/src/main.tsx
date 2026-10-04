import React from "react";
import { createRoot } from "react-dom/client";
import "./shared/theme.css";
import { AuthProvider } from "./features/identity/auth";
import { App } from "./app/App";

const rootEl = document.getElementById("root");
if (!rootEl) {
  throw new Error("missing #root element");
}

createRoot(rootEl).render(
  <React.StrictMode>
    <AuthProvider>
      <App />
    </AuthProvider>
  </React.StrictMode>,
);
