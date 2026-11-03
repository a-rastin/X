/* Keep the current route mounted until the draft's latest edits are saved. */
let guard: { isDirty: () => boolean; save: () => Promise<{ ok: boolean }> } | null = null;
let acceptedHash = "";
let pending = false;
let releasedHash: string | null = null;

export function registerUnsavedGuard(next: typeof guard): void {
  guard = next;
  if (next !== null) acceptedHash = window.location.hash;
}

export function hasUnsavedChanges(): boolean {
  return guard?.isDirty() === true;
}

export async function saveBeforeNavigation(): Promise<boolean> {
  if (guard === null) return true;
  if (pending) return false;
  pending = true;
  try {
    return (await guard.save()).ok;
  } finally {
    pending = false;
  }
}

// Registered before React's hash listeners: restoring synchronously prevents
// a changed encounter ID or route from unmounting the editor during a save.
window.addEventListener("hashchange", (event) => {
  const target = window.location.hash;
  if (releasedHash === target) {
    releasedHash = null;
    // Another edit may arrive between releasing the hash and this event.
    if (guard === null || !guard.isDirty()) {
      acceptedHash = target;
      return;
    }
  }
  if (guard === null || !guard.isDirty()) {
    acceptedHash = target;
    return;
  }
  event.stopImmediatePropagation();
  window.history.replaceState(window.history.state, "", acceptedHash);
  void saveBeforeNavigation().then((ok) => {
    if (ok) {
      releasedHash = target;
      window.location.hash = target;
    }
  });
}, true);
