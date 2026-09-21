/**
 * The browser side key vault.
 *
 * Provider credentials live in localStorage and nowhere else. They are attached
 * as request headers by lib/api.ts on their way to this app's own /api routes,
 * and they are never written to a URL, a query string, a log line or any third
 * party. Clearing the browser's site data removes them.
 *
 * The server holds them for the lifetime of one request in a context variable
 * and forgets them, so a key pasted here is spent only on the requests the
 * person who pasted it makes.
 */

const VAULT_KEY = "dissect.provider-keys.v1";
const PREFS_KEY = "dissect.prefs.v1";

/** What the user pasted for one provider. */
export interface ProviderCredential {
  /** The API token. Sent as X-LLM-Key-{provider}. */
  key?: string;
  /** An override for the provider's base URL. Sent as X-LLM-Base-{provider}. */
  base?: string;
  /** Cloudflare needs an account id as well as a token. Sent as X-LLM-Account-{provider}. */
  account?: string;
}

export type KeyVault = Record<string, ProviderCredential>;

/** Choices that only matter in this browser, kept beside the keys. */
export interface Prefs {
  provider?: string;
  model?: string;
  embedder?: string;
  parseMode?: string;
  dense?: boolean;
  ocr?: boolean;
  useLlm?: boolean;
  rerank?: boolean;
  topK?: number;
  appPassword?: string;
}

const EMPTY_VAULT: KeyVault = {};

function canStore(): boolean {
  return typeof window !== "undefined" && typeof window.localStorage !== "undefined";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readString(source: Record<string, unknown>, field: string): string | undefined {
  const value = source[field];
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed ? trimmed : undefined;
}

/** Read the vault, tolerating anything that is not the shape we wrote. */
export function loadVault(): KeyVault {
  if (!canStore()) return EMPTY_VAULT;
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(VAULT_KEY);
  } catch {
    // Private browsing, or site data blocked. Working without stored keys is
    // a supported state, so this is not worth surfacing.
    return EMPTY_VAULT;
  }
  if (!raw) return EMPTY_VAULT;

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return EMPTY_VAULT;
  }
  if (!isRecord(parsed)) return EMPTY_VAULT;

  const vault: KeyVault = {};
  for (const [provider, value] of Object.entries(parsed)) {
    if (!isRecord(value)) continue;
    const credential: ProviderCredential = {};
    const key = readString(value, "key");
    const base = readString(value, "base");
    const account = readString(value, "account");
    if (key) credential.key = key;
    if (base) credential.base = base;
    if (account) credential.account = account;
    if (Object.keys(credential).length) vault[provider] = credential;
  }
  return vault;
}

export function saveVault(vault: KeyVault): void {
  if (!canStore()) return;
  try {
    const cleaned: KeyVault = {};
    for (const [provider, credential] of Object.entries(vault)) {
      const entry: ProviderCredential = {};
      if (credential.key?.trim()) entry.key = credential.key.trim();
      if (credential.base?.trim()) entry.base = credential.base.trim();
      if (credential.account?.trim()) entry.account = credential.account.trim();
      if (Object.keys(entry).length) cleaned[provider] = entry;
    }
    window.localStorage.setItem(VAULT_KEY, JSON.stringify(cleaned));
  } catch {
    // Quota or a blocked store. Nothing here is worth failing an action over.
  }
}

export function setCredential(provider: string, credential: ProviderCredential): KeyVault {
  const vault = loadVault();
  const next = { ...vault, [provider]: credential };
  if (!credential.key && !credential.base && !credential.account) delete next[provider];
  saveVault(next);
  return next;
}

export function clearCredential(provider: string): KeyVault {
  const vault = loadVault();
  delete vault[provider];
  saveVault(vault);
  return vault;
}

export function clearAllCredentials(): void {
  if (!canStore()) return;
  try {
    window.localStorage.removeItem(VAULT_KEY);
  } catch {
    // Nothing to do; the caller already told the user what was attempted.
  }
}

/**
 * Turn the vault into request headers.
 *
 * One header per provider rather than one encoded blob, so what is being sent
 * is obvious in a network inspector and no encoding has to be invented.
 */
export function credentialHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  for (const [provider, credential] of Object.entries(loadVault())) {
    if (credential.key) headers[`X-LLM-Key-${provider}`] = credential.key;
    if (credential.base) headers[`X-LLM-Base-${provider}`] = credential.base;
    if (credential.account) headers[`X-LLM-Account-${provider}`] = credential.account;
  }
  const password = loadPrefs().appPassword;
  if (password) headers["X-App-Password"] = password;
  return headers;
}

export function loadPrefs(): Prefs {
  if (!canStore()) return {};
  try {
    const raw = window.localStorage.getItem(PREFS_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    return isRecord(parsed) ? (parsed as Prefs) : {};
  } catch {
    return {};
  }
}

export function savePrefs(prefs: Prefs): void {
  if (!canStore()) return;
  try {
    window.localStorage.setItem(PREFS_KEY, JSON.stringify({ ...loadPrefs(), ...prefs }));
  } catch {
    // See saveVault.
  }
}
