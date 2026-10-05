/* Attributed page-note HTTP client (S13, plan.md §§2.3, 4.1-4.3; FR-16, FR-22).
 *
 * Same-origin /api/v1 routes (dev/preview proxy forwards /api to the
 * backend). Mutations need an active physician session plus the per-session
 * CSRF token; reads need any active authenticated session but only the draft
 * author receives notes (403 strangers, 404 missing/discarded — enforced
 * server-side, never client-side).
 *
 * Contract (see backend/src/x_insight/cases/notes.py, read-only):
 * - POST /encounters/{id}/notes carries {page, text} only (extra=forbid —
 *   author/time are server-derived); If-Match "<revision>" is required
 *   (absent/"*"/malformed is 422, stale is 412 STALE_REVISION); fresh
 *   Idempotency-Key per submit (same key + same body replays one note, same
 *   key + changed body is 409 IDEMPOTENCY_CONFLICT); 201 returns
 *   {note, revision, server_timestamp} + ETag. Creating a note bumps the
 *   encounter revision so S07 autosave stays coherent.
 * - GET /encounters/{id}/notes lists author-only notes with optional
 *   ?page= filter and bounded limit/offset (25/100), stable
 *   (created_at, id) order, {items, total, revision} + ETag. Note shape:
 *   {id, encounter_id, page, author_id, author_display, created_at, text}.
 * - Append-only: no edit/delete endpoint exists; correction is a new note.
 * - Bounds: page must be in ALLOWED_PAGES, text non-empty verbatim
 *   (literal markup preserved) at most 2000 chars (422 with field_errors).
 *
 * Notes live in their own table, never in draft_data and never in the
 * analysis-visible history channel. No snapshot machinery here: the explicit
 * serializer exclusion lives in backend notes.py
 * (exclude_notes_from_analysis), and S40/S41/S59 are the mandatory
 * end-to-end note-noninterference checks (each must prove page notes never
 * leak into analysis-visible history, prompts, CPTs, DDI, or proposal
 * selection, and that a note-only change does not invalidate probability
 * acceptance). Those sessions implement the checks; this module only calls
 * the notes endpoints.
 */

import {
  ApiError,
  CSRF_HEADER,
  getCsrfToken,
  newIdempotencyKey,
} from "../identity/api";
import { formatIfMatch } from "../encounters/api";

export interface PageNote {
  id: string;
  encounter_id: string;
  page: string;
  author_id: string;
  author_display: string;
  created_at: string;
  text: string;
}

export interface NotesListResult {
  items: PageNote[];
  total: number;
  revision: number;
}

export interface NoteCreateResult {
  note: PageNote;
  revision: number;
  server_timestamp: string;
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  ifMatch?: number;
  idempotencyKey?: string;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = getCsrfToken();
  if (token) {
    headers[CSRF_HEADER] = token;
  }
  if (options.ifMatch !== undefined) {
    headers["If-Match"] = formatIfMatch(options.ifMatch);
  }
  if (options.idempotencyKey !== undefined) {
    headers["Idempotency-Key"] = options.idempotencyKey;
  }
  const response = await fetch(`/api/v1${path}`, {
    method: options.method ?? "GET",
    credentials: "include",
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }
  if (!response.ok) {
    const body = (payload ?? {}) as {
      code?: string;
      message?: string;
      field_errors?: Record<string, string[]>;
    };
    throw new ApiError(
      response.status,
      body.code ?? "REQUEST_FAILED",
      body.message ?? `Request failed (${response.status}).`,
      body.field_errors ?? {},
    );
  }
  return (payload ?? {}) as T;
}

/** Author-only note list for one page (reads need no CSRF, like GET previews). */
export async function listNotes(
  encounterId: string,
  page: string,
): Promise<NotesListResult> {
  const query = `?page=${encodeURIComponent(page)}&limit=100`;
  return request<NotesListResult>(
    `/encounters/${encodeURIComponent(encounterId)}/notes${query}`,
  );
}

export interface CreateNoteOptions {
  /** Reuse only for retry of the same attempt; new text needs a fresh key. */
  idempotencyKey?: string;
}

/** Author-only append of {page, text} (If-Match-fenced, revision-bumping).
 * Fresh key per attempt unless retrying the same attempt. */
export async function createNote(
  encounterId: string,
  page: string,
  text: string,
  revision: number,
  options: CreateNoteOptions = {},
): Promise<NoteCreateResult> {
  return request<NoteCreateResult>(
    `/encounters/${encodeURIComponent(encounterId)}/notes`,
    {
      method: "POST",
      ifMatch: revision,
      idempotencyKey: options.idempotencyKey ?? newIdempotencyKey(),
      body: { page, text },
    },
  );
}

export { newIdempotencyKey };
