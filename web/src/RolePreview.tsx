import { useState } from "react";
import { AuthProvider } from "./features/identity/auth";
import { RoleSelector } from "./features/identity/RoleSelector";
import { RegistrationForm } from "./features/patients/RegistrationForm";
import { LanguageProvider } from "./shared/i18n";

export function RolePreview() {
  const [value, setValue] = useState("type_a");
  if (new URLSearchParams(location.search).get("view") === "sign-in") {
    return <main className="xi-auth-layout"><section className="xi-auth"><h2 className="xi-section-title">Sign in</h2><p className="xi-auth-subtitle">Choose your workspace access</p><RoleSelector value={value} onChange={setValue} label="Choose type" physician="Physician" admin="Admin" /><p className="xi-hint">Signing in as {value === "type_a" ? "Physician" : "Admin"}</p><label className="xi-label" htmlFor="preview-username">Username</label><input className="xi-input" id="preview-username" /><label className="xi-label" htmlFor="preview-password">Password</label><input className="xi-input" id="preview-password" type="password" /></section></main>;
  }
  return <LanguageProvider><AuthProvider><RegistrationForm /></AuthProvider></LanguageProvider>;
}
