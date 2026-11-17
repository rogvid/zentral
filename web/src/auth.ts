// Sign-in: OpenID Connect authorization code flow with PKCE (RFC 7636).
//
// One implementation for every identity provider — Microsoft Entra ID, any
// other OIDC provider, and the built-in development IdP. The backend tells us
// which provider to use (`/api/v1/config`); endpoints come from the provider's
// discovery document.
//
// Security notes
// * Public client: there is no client secret. PKCE (S256) binds the code to
//   this browser; `state` binds the response to the request we made.
// * Tokens are kept in sessionStorage (gone when the tab closes) and are only
//   ever sent to this origin's API. No cookies are used, so there is no CSRF
//   surface. The strict Content-Security-Policy is what protects them from
//   script injection.
// * The ID token is not used at all: who the user is, and what they may do, is
//   decided by the API from the access token on every request.

import type { AppConfig, AuthConfig } from "./types";

export interface Session {
  getToken(): Promise<string>;
  signOut(): Promise<void>;
}

interface Discovery {
  issuer: string;
  authorization_endpoint: string;
  token_endpoint: string;
  end_session_endpoint?: string;
}

interface Stored {
  access_token: string;
  refresh_token?: string;
  expires_at: number;
}

interface Pending {
  state: string;
  verifier: string;
  returnTo: string;
}

const TOKENS = "zentral.auth.tokens";
const PENDING = "zentral.auth.pending";
const SKEW_MS = 60_000;

export async function loadConfig(): Promise<AppConfig> {
  const response = await fetch("/api/v1/config");
  if (!response.ok) throw new Error("Could not load configuration");
  return (await response.json()) as AppConfig;
}

export function base64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function randomToken(byteLength = 32): string {
  return base64Url(crypto.getRandomValues(new Uint8Array(byteLength)));
}

export async function challengeFor(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return base64Url(new Uint8Array(digest));
}

function redirectUri(): string {
  return `${window.location.origin}/`;
}

function read<T>(key: string): T | null {
  try {
    const raw = sessionStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

async function discover(auth: AuthConfig): Promise<Discovery> {
  const response = await fetch(`${auth.authority.replace(/\/$/, "")}/.well-known/openid-configuration`, {
    credentials: "omit",
  });
  if (!response.ok) throw new Error("Could not reach the identity provider");
  const document = (await response.json()) as Discovery;
  // Defend against a swapped discovery document (IdP mix-up).
  if (document.issuer !== auth.authority) throw new Error("Identity provider metadata does not match");
  return document;
}

async function beginSignIn(auth: AuthConfig, discovery: Discovery): Promise<never> {
  const pending: Pending = {
    state: randomToken(16),
    verifier: randomToken(48),
    returnTo: window.location.hash,
  };
  sessionStorage.setItem(PENDING, JSON.stringify(pending));
  const params = new URLSearchParams({
    response_type: "code",
    client_id: auth.client_id,
    redirect_uri: redirectUri(),
    scope: auth.scopes.join(" "),
    state: pending.state,
    code_challenge: await challengeFor(pending.verifier),
    code_challenge_method: "S256",
  });
  window.location.assign(`${discovery.authorization_endpoint}?${params.toString()}`);
  return new Promise<never>(() => undefined); // the page is navigating away
}

async function tokenRequest(discovery: Discovery, body: Record<string, string>): Promise<Stored> {
  const response = await fetch(discovery.token_endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams(body).toString(),
    credentials: "omit",
  });
  if (!response.ok) throw new Error("The identity provider rejected the sign-in");
  const data = (await response.json()) as { access_token?: string; refresh_token?: string; expires_in?: number };
  if (!data.access_token) throw new Error("The identity provider returned no access token");
  const stored: Stored = {
    access_token: data.access_token,
    refresh_token: data.refresh_token ?? body.refresh_token,
    expires_at: Date.now() + (data.expires_in ?? 300) * 1000,
  };
  sessionStorage.setItem(TOKENS, JSON.stringify(stored));
  return stored;
}

/** If the IdP just redirected back with `?code=…`, finish the exchange. */
async function completeSignIn(auth: AuthConfig, discovery: Discovery): Promise<void> {
  const params = new URLSearchParams(window.location.search);
  const code = params.get("code");
  const error = params.get("error");
  if (!code && !error) return;
  const pending = read<Pending>(PENDING);
  sessionStorage.removeItem(PENDING);
  // Remove the one-time code from the address bar and history straight away.
  window.history.replaceState(null, "", `${window.location.pathname}${pending?.returnTo ?? ""}`);
  if (error) throw new Error(`Sign-in was not completed (${error.slice(0, 60)})`);
  if (!pending || params.get("state") !== pending.state) {
    throw new Error("Sign-in response did not match the request. Please try again.");
  }
  await tokenRequest(discovery, {
    grant_type: "authorization_code",
    code: code ?? "",
    redirect_uri: redirectUri(),
    client_id: auth.client_id,
    code_verifier: pending.verifier,
  });
}

let starting: Promise<Session> | null = null;

/**
 * Resolves once signed in. Redirects to the identity provider when necessary.
 *
 * Single-flight: the authorization code can be redeemed exactly once, so
 * concurrent callers (React runs effects twice in development) must share one
 * attempt — otherwise the second would see "no token yet" and start over.
 */
export function startSession(config: AppConfig): Promise<Session> {
  starting ??= openSession(config);
  return starting;
}

async function openSession(config: AppConfig): Promise<Session> {
  const auth = config.auth;
  const discovery = await discover(auth);
  await completeSignIn(auth, discovery);
  if (!read<Stored>(TOKENS)) await beginSignIn(auth, discovery);

  let refreshing: Promise<Stored> | null = null;
  return {
    async getToken() {
      const current = read<Stored>(TOKENS);
      if (current && current.expires_at - SKEW_MS > Date.now()) return current.access_token;
      if (current?.refresh_token) {
        // One refresh at a time: refresh tokens are typically single use.
        refreshing ??= tokenRequest(discovery, {
          grant_type: "refresh_token",
          refresh_token: current.refresh_token,
          client_id: auth.client_id,
          scope: auth.scopes.join(" "),
        }).finally(() => {
          refreshing = null;
        });
        try {
          return (await refreshing).access_token;
        } catch {
          sessionStorage.removeItem(TOKENS);
        }
      }
      return beginSignIn(auth, discovery);
    },
    async signOut() {
      sessionStorage.removeItem(TOKENS);
      if (discovery.end_session_endpoint) {
        const params = new URLSearchParams({ client_id: auth.client_id, post_logout_redirect_uri: redirectUri() });
        window.location.assign(`${discovery.end_session_endpoint}?${params.toString()}`);
      } else {
        window.location.assign(redirectUri());
      }
    },
  };
}
