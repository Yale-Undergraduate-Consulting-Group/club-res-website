import { ApiError } from '../api';

/** What every AI-drafting surface says when the model cannot be reached. The
 *  campaign drafting route sends this exact text; the Studio routes send the
 *  provider's own 503 message, so the surfaces compare the status, not the text. */
export const AI_DRAFTING_UNAVAILABLE = 'AI drafting is unavailable right now — use a template.';

export function aiDraftingUnavailable(error: unknown): boolean {
  return error instanceof ApiError && error.status === 503;
}
