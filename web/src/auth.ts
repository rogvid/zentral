// Authentication.
//
// Production: Microsoft Entra ID via MSAL (authorization code flow with PKCE).
// Tokens live in sessionStorage (cleared when the tab closes) and are attached
// only to same-origin API calls. There are no cookies, hence no CSRF surface.
//
// Development: the backend's dev issuer hands out tokens for fixed personas.

import type { AccountInfo } from "@azure/msal-browser";
import type { AppConfig } from "./types";

export interface Session {
  getToken(): Promise<string>;
  signOut(): Promise<void>;
}

const PERSONA_KEY = "zentral.dev.persona";

export async function loadConfig(): Promise<AppConfig> {
  const response = await fetch("/api/v1/config");
  if (!response.ok) throw new Error("Could not load configuration");
  return (await response.json()) as AppConfig;
}

export function currentPersona(): string | null {
  return sessionStorage.getItem(PERSONA_KEY);
}

export function choosePersona(persona: string): void {
  sessionStorage.setItem(PERSONA_KEY, persona);
}

function devSession(persona: string): Session {
  let token: string | null = null;
  let fetchedAt = 0;
  return {
    async getToken() {
      if (!token || Date.now() - fetchedAt > 30 * 60 * 1000) {
        const response = await fetch("/api/v1/dev/token", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ persona }),
        });
        if (!response.ok) throw new Error("Could not obtain a development token");
        token = ((await response.json()) as { access_token: string }).access_token;
        fetchedAt = Date.now();
      }
      return token;
    },
    async signOut() {
      sessionStorage.removeItem(PERSONA_KEY);
      window.location.reload();
    },
  };
}

async function entraSession(config: AppConfig): Promise<Session | null> {
  // Loaded on demand so the sign-in library is not shipped to the dev persona flow.
  const { PublicClientApplication } = await import("@azure/msal-browser");
  const msal = new PublicClientApplication({
    auth: {
      clientId: config.spa_client_id,
      authority: config.authority,
      redirectUri: window.location.origin,
      postLogoutRedirectUri: window.location.origin,
    },
    cache: { cacheLocation: "sessionStorage" },
  });
  await msal.initialize();
  const redirect = await msal.handleRedirectPromise();
  const account: AccountInfo | null =
    redirect?.account ?? msal.getActiveAccount() ?? msal.getAllAccounts()[0] ?? null;
  const scopes = [config.api_scope];
  if (!account) {
    await msal.loginRedirect({ scopes });
    return null; // the browser is navigating away
  }
  msal.setActiveAccount(account);
  return {
    async getToken() {
      try {
        return (await msal.acquireTokenSilent({ scopes, account })).accessToken;
      } catch {
        await msal.acquireTokenRedirect({ scopes, account });
        throw new Error("Redirecting to sign in");
      }
    },
    async signOut() {
      await msal.logoutRedirect({ account });
    },
  };
}

/** Returns null when the user still has to pick a persona (dev) or is being redirected (Entra). */
export async function startSession(config: AppConfig): Promise<Session | null> {
  if (config.auth_mode === "dev") {
    const persona = currentPersona();
    return persona && config.dev_personas.includes(persona) ? devSession(persona) : null;
  }
  return entraSession(config);
}
