// Access credentials (operator name + tokens) persisted to localStorage.
// Tokens are sent as X-Operator-Token / X-Admin-Token headers per the contract.
// Nothing here is a secret store: it is the same trust model as the backend's
// static tokens, kept only in the operator's own browser.

const STORAGE_KEY = "jalani.access.v1";

export interface AccessInfo {
  operatorName: string;
  operatorToken: string;
  adminToken: string;
}

export const emptyAccess: AccessInfo = {
  operatorName: "",
  operatorToken: "",
  adminToken: "",
};

export function loadAccess(): AccessInfo {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...emptyAccess };
    const parsed = JSON.parse(raw) as Partial<AccessInfo>;
    return { ...emptyAccess, ...parsed };
  } catch {
    return { ...emptyAccess };
  }
}

export function saveAccess(info: AccessInfo): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(info));
  } catch {
    // Ignore storage failures (private mode, quota); the app still works
    // in-memory for the session.
  }
}
