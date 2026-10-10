import { ApiError } from "@/lib/api";

import { Notice } from "./ui";

/** What went wrong, in words a visitor can act on. */
export function describeError(err: unknown): string {
  if (!(err instanceof ApiError)) return "Something went wrong.";
  if (err.status === 0) return "Cannot reach the API. It scales to zero when idle; try again in a few seconds.";
  if (err.status === 429) {
    const wait = err.retryAfterS ? ` Try again in ${Math.ceil(err.retryAfterS / 60)} min.` : "";
    return `${err.detail ?? err.title}.${wait}`;
  }
  return err.detail ? `${err.title}: ${err.detail}` : err.title;
}

export function ErrorNote({ error }: { error: unknown }) {
  return <Notice tone="bad">{describeError(error)}</Notice>;
}
