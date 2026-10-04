/* In-app hash-route navigation guard (S07).
 *
 * `beforeunload` covers tab close/reload; hash-route changes within the SPA
 * never fire it, so the draft page registers its unsaved state here and
 * every in-app navigation checks `shouldBlockNavigation()` first. The check
 * uses `window.confirm` (testable via Playwright dialogs) and returns true
 * when navigation must be cancelled.
 */

let unsavedProvider: (() => boolean) | null = null;

export function registerUnsavedGuard(provider: (() => boolean) | null): void {
  unsavedProvider = provider;
}

export function hasUnsavedChanges(): boolean {
  try {
    return unsavedProvider?.() === true;
  } catch {
    return false;
  }
}

export function shouldBlockNavigation(): boolean {
  if (!hasUnsavedChanges()) {
    return false;
  }
  return !window.confirm(
    "You have unsaved draft changes. Leave without saving?",
  );
}
