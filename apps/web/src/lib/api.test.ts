import { afterEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "./api";

afterEach(() => vi.unstubAllGlobals());

const respond = (status: number, body: unknown, headers: Record<string, string> = {}) =>
  vi.fn().mockImplementation(
    async () =>
      new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/problem+json", ...headers },
      }),
  );

describe("api", () => {
  it("turns a problem+json 429 into an ApiError with Retry-After", async () => {
    vi.stubGlobal("fetch", respond(429, { title: "Daily run limit reached", detail: "30 runs today" }, { "Retry-After": "3600" }));
    const err = await api.replay("S1").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 429, title: "Daily run limit reached", retryAfterS: 3600 });
  });

  it("reports an unreachable API as status 0", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(api.incidents()).rejects.toMatchObject({ status: 0, title: "Cannot reach the API" });
  });

  it("sends the operator token only on decisions, as X-Admin-Token", async () => {
    const fetch = respond(200, { id: 7 });
    vi.stubGlobal("fetch", fetch);
    await api.decide(7, "approve", "tok", "lokesh");
    const [url, init] = fetch.mock.calls[0];
    expect(url).toMatch(/\/api\/v1\/runs\/7\/approve$/);
    expect(init.headers["X-Admin-Token"]).toBe("tok");
    expect(JSON.parse(init.body)).toEqual({ by: "lokesh" });

    await api.replay("S1");
    expect(fetch.mock.calls[1][1].headers["X-Admin-Token"]).toBeUndefined();
  });
});
