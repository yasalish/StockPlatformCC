/**
 * http.ts — the one place the islands talk to the API (review finding M-3).
 *
 * Every island used to call `fetch` directly:
 *
 *     const res = await fetch(url, { headers: { Accept: "application/json" } });
 *     if (!res.ok) throw new Error(`HTTP ${res.status}`);
 *
 * Three problems with that, all user-visible:
 *
 *   NO BOUND. A bare fetch waits as long as the network does. Against
 *   Gunicorn's 120 s timeout a slow request left the visitor looking at a
 *   hidden panel with no spinner and no end — the page simply never resolved.
 *
 *   NO RETRY. A single dropped packet on a mobile connection — the normal case
 *   for this audience — turned into a failed page rather than a second attempt.
 *
 *   429 INDISTINGUISHABLE FROM FAILURE. `HTTP 429` fell into the same branch as
 *   everything else, so a rate-limited user was shown "the table did not load,
 *   try reloading" — advice that makes their situation worse, since reloading
 *   is another request against the same limit. The filter designer's own
 *   concurrency guard returns 429 by design, so this is a routine path, not an
 *   edge case.
 *
 * The timeout is deliberately shorter than the server's. A request the server
 * is still working on at 15 s is one the visitor has already given up on, and
 * abandoning it frees the browser's connection for the retry.
 */

/** Everything the caller needs to decide what to tell the user. */
export class HttpError extends Error {
  /** HTTP status, or 0 for a network failure / timeout. */
  readonly status: number;
  /** True when the request was aborted by our own timeout. */
  readonly timedOut: boolean;
  /** Seconds the server asked us to wait, when it said. */
  readonly retryAfter: number;
  /** The `error` field from the response body, when the server sent one.
   *
   *  These are already written for the person reading them — «یک بک‌تست دیگر در
   *  حال اجراست…» says more than any status-code mapping could — so when it is
   *  present userMessage() prefers it over its own wording. */
  readonly serverMessage: string;

  constructor(
    message: string,
    status: number,
    opts?: { timedOut?: boolean; retryAfter?: number; serverMessage?: string },
  ) {
    super(message);
    this.name = "HttpError";
    this.status = status;
    this.timedOut = opts?.timedOut ?? false;
    this.retryAfter = opts?.retryAfter ?? 0;
    this.serverMessage = opts?.serverMessage ?? "";
  }
}

const DEFAULT_TIMEOUT_MS = 15_000;

/** 5xx and a dropped connection are worth retrying; a 4xx is the caller's fault
 *  and will fail identically the second time. 429 is excluded on purpose — it
 *  means "you are asking too often", and retrying is the one thing that makes
 *  it worse. */
function worthRetrying(err: HttpError): boolean {
  if (err.status === 0) return true;            // network failure or timeout
  return err.status >= 500 && err.status < 600;
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

async function once<T>(url: string, init: RequestInit, timeoutMs: number): Promise<T> {
  // AbortController is the only way to put a ceiling on fetch. Older Android
  // WebViews in this market do have it; the optional chain below is for the
  // handful that do not, where the request stays unbounded as it was before —
  // strictly no worse than the previous behaviour.
  const ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
  const timer = ctrl ? setTimeout(() => ctrl.abort(), timeoutMs) : null;

  try {
    const res = await fetch(url, {
      ...init,
      headers: { Accept: "application/json", ...(init.headers ?? {}) },
      signal: ctrl?.signal,
    });

    if (!res.ok) {
      // `Retry-After` is a header the server may set; our own error bodies
      // carry `retry_after` and `error`, so all three are read. One clone, not
      // one per field — the body can only be consumed once.
      let retryAfter = Number(res.headers.get("Retry-After") ?? 0) || 0;
      let serverMessage = "";
      try {
        const body = (await res.clone().json()) as { error?: string; retry_after?: number };
        serverMessage = typeof body?.error === "string" ? body.error : "";
        if (!retryAfter) retryAfter = Number(body?.retry_after ?? 0) || 0;
      } catch {
        /* an error response without a JSON body is still an error response */
      }
      throw new HttpError(`HTTP ${res.status}`, res.status, { retryAfter, serverMessage });
    }

    return (await res.json()) as T;
  } catch (err) {
    if (err instanceof HttpError) throw err;
    const aborted = err instanceof DOMException && err.name === "AbortError";
    throw new HttpError(
      aborted ? "request timed out" : String((err as Error)?.message ?? err),
      0,
      { timedOut: aborted },
    );
  } finally {
    if (timer !== null) clearTimeout(timer);
  }
}

/**
 * GET (or POST) JSON with a bounded wait and one retry.
 *
 * The retry waits 400-900 ms — jittered, so a market-open spike that fails a
 * thousand clients at once does not have them all come back on the same
 * millisecond and fail again together.
 */
export async function getJson<T>(
  url: string,
  init: RequestInit = {},
  opts: { timeoutMs?: number; retries?: number } = {},
): Promise<T> {
  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const retries = opts.retries ?? 1;

  let last: HttpError | null = null;
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    try {
      return await once<T>(url, init, timeoutMs);
    } catch (err) {
      last = err as HttpError;
      if (attempt === retries || !worthRetrying(last)) break;
      await sleep(400 + Math.random() * 500);
    }
  }
  throw last ?? new HttpError("request failed", 0);
}

/**
 * POST JSON and read JSON back.
 *
 * A longer default timeout than getJson, because the two callers are the filter
 * run and the backtest — the review measures one market-wide run at "low
 * seconds", and a backtest replays years of history. Retries default to ZERO:
 * these are expensive, non-idempotent-feeling operations, and automatically
 * running a second market-wide scan because the first one was slow is exactly
 * the pile-up the server's slot guard exists to prevent.
 */
export async function postJson<T>(
  url: string,
  body: unknown,
  opts: { timeoutMs?: number } = {},
): Promise<T> {
  return getJson<T>(
    url,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
    { timeoutMs: opts.timeoutMs ?? 60_000, retries: 0 },
  );
}

/**
 * The Persian sentence to show a visitor for a given failure.
 *
 * The 429 case is the reason this function exists: it tells the user to WAIT,
 * and deliberately does not suggest reloading.
 */
export function userMessage(err: unknown): string {
  if (err instanceof HttpError) {
    // The server's own sentence wins when there is one: it knows which of the
    // several 400s and 429s this is, and it is already in Persian.
    if (err.serverMessage) return err.serverMessage;
    if (err.status === 429) {
      const wait = err.retryAfter
        ? `حدود ${faDigits(String(err.retryAfter))} ثانیه`
        : "چند لحظه";
      return `درخواست‌ها بیش از حد مجاز است. لطفاً ${wait} صبر کنید و دوباره تلاش کنید — صفحه را دوباره بارگذاری نکنید.`;
    }
    if (err.status === 401) {
      return "نشست شما پایان یافته است. لطفاً دوباره وارد شوید.";
    }
    if (err.timedOut) {
      return "پاسخ سرور بیش از حد طول کشید. اتصال خود را بررسی کنید و دوباره تلاش کنید.";
    }
    if (err.status === 0) {
      return "اتصال به سرور برقرار نشد. اتصال اینترنت خود را بررسی کنید.";
    }
    if (err.status >= 500) {
      return "سرور در پاسخ‌دهی با خطا روبه‌رو شد. چند لحظه بعد دوباره تلاش کنید.";
    }
  }
  return "بارگذاری داده‌ها ناموفق بود.";
}

/** Latin digits to Persian, for the one number userMessage() prints. Kept local
 *  rather than imported from format.ts so this module has no dependencies and
 *  can be used by the vanilla pages as a plain ES module. */
function faDigits(s: string): string {
  return s.replace(/[0-9]/g, (d) => "۰۱۲۳۴۵۶۷۸۹"[Number(d)]);
}

/**
 * Reveal the server-rendered fallback and say why, in place of the template's
 * generic "reload the page" line.
 *
 * The templates carry a `[data-role="island-error"]` paragraph for this. When
 * it is present the specific message goes there and the generic advice is
 * hidden; when it is absent — a template not yet updated — the fallback still
 * appears exactly as it did before, so this can never make a page worse.
 */
export function revealIslandFallback(err: unknown): void {
  const msg = userMessage(err);
  document.querySelectorAll<HTMLElement>(".bn-island-fallback").forEach((panel) => {
    panel.hidden = false;
    const slot = panel.querySelector<HTMLElement>('[data-role="island-error"]');
    if (!slot) return;
    slot.textContent = msg;
    slot.hidden = false;
    // Hide the generic advice, which for a 429 is actively wrong.
    panel.querySelectorAll<HTMLElement>('[data-role="island-generic"]').forEach((n) => {
      n.hidden = true;
    });
  });
}
