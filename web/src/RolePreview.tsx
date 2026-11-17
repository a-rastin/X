import { useState } from "react";
import { RoleSelector } from "./features/identity/RoleSelector";

export function RolePreview() {
  const [value, setValue] = useState("type_a");
  return <main className="xi-auth-layout"><section className="xi-auth"><h2 className="xi-section-title">Sign in</h2><p className="xi-auth-subtitle">Choose your workspace access</p><RoleSelector value={value} onChange={setValue} label="Choose type" physician="Physician" admin="Admin" /><p className="xi-hint">Signing in as {value === "type_a" ? "Physician" : "Admin"}</p><label className="xi-label" htmlFor="preview-username">Username</label><input className="xi-input" id="preview-username" /><label className="xi-label" htmlFor="preview-password">Password</label><input className="xi-input" id="preview-password" type="password" /></section></main>;
}
