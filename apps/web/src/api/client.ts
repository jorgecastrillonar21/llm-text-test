/**
 * Thin fetch wrapper.
 *
 * All requests go to same-origin `/api/*`, which the Vite dev server proxies to
 * the backend. The phone therefore only needs one address, and Ollama/ComfyUI
 * are never reachable from the browser.
 */

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string,
    readonly retryable: boolean,
    readonly provider?: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

/** The backend is unreachable — distinct from an error the backend reported. */
export class OfflineError extends Error {
  constructor() {
    super('Cannot reach the API');
    this.name = 'OfflineError';
  }
}

interface ErrorBody {
  error?: string;
  detail?: string | { msg?: string }[];
  provider?: string;
  retryable?: boolean;
}

function readDetail(body: ErrorBody, fallback: string): string {
  if (typeof body.detail === 'string') return body.detail;
  if (Array.isArray(body.detail) && body.detail[0]?.msg) return body.detail[0].msg;
  return fallback;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...init?.headers },
    });
  } catch {
    throw new OfflineError();
  }

  if (!response.ok) {
    let body: ErrorBody = {};
    try {
      body = (await response.json()) as ErrorBody;
    } catch {
      // Non-JSON error body; fall through to the status-based message.
    }
    throw new ApiError(
      readDetail(body, `Request failed with status ${response.status}`),
      response.status,
      body.error ?? 'http_error',
      body.retryable ?? false,
      body.provider,
    );
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body: unknown) =>
    request<T>(path, { method: 'POST', body: JSON.stringify(body) }),
};
