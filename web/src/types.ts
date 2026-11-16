export type Status = "new" | "open" | "pending" | "resolved" | "closed";
export type Priority = "low" | "normal" | "high" | "urgent";

export interface AuthConfig {
  mode: "entra" | "oidc" | "dev";
  authority: string;
  client_id: string;
  scopes: string[];
}

export interface AppConfig {
  auth: AuthConfig;
  ticket_prefix: string;
  categories: string[];
  require_system_to_resolve: boolean;
}

export interface Me {
  subject: string;
  kind: string;
  display_name: string;
  email: string;
  roles: string[];
  permissions: string[];
  team_ids: string[];
}

export interface Requester {
  id: string;
  display_name: string;
  email: string | null;
}

export interface Ticket {
  id: number;
  key: string;
  subject: string;
  status: Status;
  priority: Priority;
  category: string | null;
  tags: string[];
  channel: string;
  team_id: string | null;
  team_name: string | null;
  assignee_id: string | null;
  assignee_name: string | null;
  system_id: string | null;
  system_key: string | null;
  system_name: string | null;
  requester: Requester | null;
  ai_category: string | null;
  ai_priority: Priority | null;
  ai_system_key: string | null;
  ai_confidence: number | null;
  ai_summary: string | null;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
  pii_redacted: boolean;
}

export interface TicketPage {
  items: Ticket[];
  total: number;
  limit: number;
  offset: number;
}

export interface Message {
  id: string;
  kind: "inbound" | "outbound" | "note";
  body: string;
  author_name: string;
  author_type: "requester" | "staff" | "system";
  delivery_status: string;
  created_at: string;
}

export interface Team {
  id: string;
  name: string;
  entra_group_id: string | null;
  member_ids: string[];
}

export interface SystemInfo {
  id: string;
  key: string;
  name: string;
  description: string;
  keywords: string[];
  default_team_id: string | null;
  active: boolean;
}

export interface User {
  id: string;
  kind: string;
  display_name: string;
  email: string;
}

export interface ConnectorInfo {
  name: string;
  capabilities: string[];
  secret_fields: string[];
  required_secrets: string[];
  uses_ingest_token: boolean;
}

export interface Source {
  id: string;
  name: string;
  connector: string;
  config: Record<string, unknown>;
  default_team_id: string | null;
  default_system_id: string | null;
  enabled: boolean;
  has_secrets: boolean;
  has_token: boolean;
  hook_url: string | null;
  ingest_token?: string | null;
}

export interface Rule {
  id: string;
  name: string;
  position: number;
  enabled: boolean;
  conditions: Record<string, unknown>;
  actions: Record<string, unknown>;
}

export interface AuditEvent {
  seq: number;
  occurred_at: string;
  actor_id: string;
  actor_kind: string;
  action: string;
  resource_type: string;
  resource_id: string;
  outcome: "success" | "denied" | "failure";
  channel: string;
  ip: string;
  details: Record<string, unknown>;
}

export interface Contact {
  id: string;
  email: string | null;
  display_name: string | null;
  created_at: string;
  erased_at: string | null;
  ticket_count: number;
}
