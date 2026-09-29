import { createContext, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { loadAccess, saveAccess, emptyAccess, type AccessInfo } from "../lib/auth";

interface AccessContextValue extends AccessInfo {
  update: (patch: Partial<AccessInfo>) => void;
  hasOperatorToken: boolean;
  hasAdminToken: boolean;
}

const AccessContext = createContext<AccessContextValue | null>(null);

export function AccessProvider({ children }: { children: ReactNode }) {
  const [access, setAccess] = useState<AccessInfo>(() => loadAccess());

  useEffect(() => {
    saveAccess(access);
  }, [access]);

  function update(patch: Partial<AccessInfo>) {
    setAccess((prev) => ({ ...prev, ...patch }));
  }

  const value: AccessContextValue = {
    ...access,
    update,
    hasOperatorToken: Boolean(access.operatorToken),
    hasAdminToken: Boolean(access.adminToken),
  };

  return <AccessContext.Provider value={value}>{children}</AccessContext.Provider>;
}

export function useAccess(): AccessContextValue {
  const ctx = useContext(AccessContext);
  if (!ctx) {
    // Defensive fallback so a component never crashes if rendered outside
    // the provider during development.
    return { ...emptyAccess, update: () => {}, hasOperatorToken: false, hasAdminToken: false };
  }
  return ctx;
}
