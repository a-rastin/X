import React from "react";
import { createRoot } from "react-dom/client";
import "@astryxdesign/core/reset.css";
import "@astryxdesign/core/astryx.css";
import "./shared/astryx/built/academic.css";
import "./shared/theme.css";
import { AuthProvider } from "./features/identity/auth";
import { App } from "./app/App";
import { LanguageProvider } from "./shared/i18n";
import { RolePreview } from "./RolePreview";

const rootEl = document.getElementById("root");
if (!rootEl) {
  throw new Error("missing #root element");
}

createRoot(rootEl).render(
  import.meta.env.DEV && location.pathname === "/ui-preview" ? <RolePreview /> : <React.StrictMode>
    <LanguageProvider>
      <AuthProvider>
        <App />
      </AuthProvider>
    </LanguageProvider>
  </React.StrictMode>,
);
