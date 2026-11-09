export function supportsLegacyWizard(workflowVersion: unknown): boolean {
  return workflowVersion === "legacy-v1";
}
