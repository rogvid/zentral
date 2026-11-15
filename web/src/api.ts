import type { Session } from "./auth";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

function describe(status: number, body: unknown): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") {
    const missing = (body as { missing_permission?: string }).missing_permission;
    return missing ? `You do not have permission to do this (${missing}).` : detail;
  }
  if (Array.isArray(detail)) {
    return detail
      .map((e: { loc?: unknown[]; msg?: string }) => `${(e.loc ?? []).slice(1).join(".")}: ${e.msg ?? ""}`)
      .join("; ");
  }
  return `Request failed (${status})`;
}

export class Api {
  constructor(private readonly session: Session) {}

  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    // Paths are always relative: the token is never sent to another origin.
    const response = await fetch(`/api/v1${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${await this.session.getToken()}`,
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: "omit",
      cache: "no-store",
    });
    if (response.status === 204) return undefined as T;
    const payload: unknown = await response.json().catch(() => null);
    if (!response.ok) throw new ApiError(response.status, describe(response.status, payload));
    return payload as T;
  }

  get<T>(path: string): Promise<T> {
    return this.request<T>("GET", path);
  }
  post<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>("POST", path, body ?? {});
  }
  patch<T>(path: string, body: unknown): Promise<T> {
    return this.request<T>("PATCH", path, body);
  }
  put<T>(path: string, body: unknown): Promise<T> {
    return this.request<T>("PUT", path, body);
  }
  delete(path: string): Promise<void> {
    return this.request<void>("DELETE", path);
  }
}
