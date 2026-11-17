import type { AttachmentInfo } from "../types";

// The portal talks only to /api/v1/portal, with its session in an HttpOnly
// cookie the page cannot read. Every call carries the portal header, which the
// server requires as protection against cross-site request forgery.

export class PortalError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function portal<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api/v1/portal${path}`, {
    method,
    headers: {
      "X-Zentral-Portal": "1",
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "same-origin",
    cache: "no-store",
  });
  if (response.status === 204) return undefined as T;
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = (payload as { detail?: unknown } | null)?.detail;
    throw new PortalError(response.status, typeof detail === "string" ? detail : "Something went wrong. Please try again.");
  }
  return payload as T;
}

/** Send a file (raw body) to one of your requests. */
export async function portalUpload<T>(ticketId: number, file: File): Promise<T> {
  const response = await fetch(`/api/v1/portal/tickets/${ticketId}/attachments?filename=${encodeURIComponent(file.name)}`, {
    method: "POST",
    headers: { "X-Zentral-Portal": "1", "Content-Type": "application/octet-stream" },
    body: file,
    credentials: "same-origin",
    cache: "no-store",
  });
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = (payload as { detail?: unknown } | null)?.detail;
    throw new PortalError(response.status, typeof detail === "string" ? `${file.name}: ${detail}` : `${file.name} could not be sent`);
  }
  return payload as T;
}

export async function portalFile(ticketId: number, attachmentId: string): Promise<Blob> {
  const response = await fetch(`/api/v1/portal/tickets/${ticketId}/attachments/${attachmentId}`, {
    headers: { "X-Zentral-Portal": "1" },
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!response.ok) throw new PortalError(response.status, "The file could not be opened");
  return response.blob();
}

export interface Profile {
  display_name: string;
  email: string;
}

export interface Request {
  id: number;
  key: string;
  subject: string;
  status: "new" | "open" | "pending" | "resolved" | "closed";
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
  merged_into_key: string | null;
  rating: number | null;
  can_rate: boolean;
  can_reply: boolean;
}

export interface PortalMessage {
  from_you: boolean;
  author: string;
  body: string;
  created_at: string;
  attachments: AttachmentInfo[];
}

export interface RequestDetail {
  ticket: Request;
  messages: PortalMessage[];
}
