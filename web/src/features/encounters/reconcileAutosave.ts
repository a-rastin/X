/** Rebase server normalization onto edits made while the save was in flight. */
export function reconcileAutosave(
  sent: unknown,
  local: unknown,
  saved: unknown,
): unknown {
  if (JSON.stringify(sent) === JSON.stringify(local)) return saved;
  if (
    sent &&
    local &&
    saved &&
    typeof sent === "object" &&
    typeof local === "object" &&
    typeof saved === "object" &&
    !Array.isArray(sent) &&
    !Array.isArray(local) &&
    !Array.isArray(saved)
  ) {
    const before = sent as Record<string, unknown>;
    const current = local as Record<string, unknown>;
    const server = saved as Record<string, unknown>;
    return Object.fromEntries(
      Object.keys({ ...server, ...current }).map((key) => [
        key,
        reconcileAutosave(before[key], current[key], server[key]),
      ]),
    );
  }
  return local;
}
