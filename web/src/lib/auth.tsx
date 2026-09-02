import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import { api, clearToken, getToken, setToken, type User } from "@/lib/api";

interface AuthContextValue {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, fullName: string) => Promise<void>;
  logout: () => void;
  /** Re-fetches /api/auth/me and updates the shared user object — used
   * after a profile change (name, voice enrollment) so the rest of the
   * app (nav bar, etc.) doesn't stay stale until a full reload. */
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!getToken()) {
      setLoading(false);
      return;
    }
    api
      .me()
      .then(setUser)
      .catch(() => clearToken())
      .finally(() => setLoading(false));
  }, []);

  async function login(email: string, password: string) {
    const token = await api.login(email, password);
    setToken(token.access_token);
    setUser(await api.me());
  }

  async function register(email: string, password: string, fullName: string) {
    const token = await api.register(email, password, fullName);
    setToken(token.access_token);
    setUser(await api.me());
  }

  function logout() {
    clearToken();
    setUser(null);
  }

  async function refreshUser() {
    setUser(await api.me());
  }

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout, refreshUser }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}
