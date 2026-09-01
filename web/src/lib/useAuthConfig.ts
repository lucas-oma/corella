import { useEffect, useState } from "react";

import { api, type AuthConfig } from "@/lib/api";

/** Instance-wide auth config (currently just whether self-serve registration
 * is open). `null` while loading. Defaults open on fetch failure so a
 * misconfigured/unreachable API doesn't strand people on a blank screen.
 */
export function useAuthConfig(): AuthConfig | null {
  const [config, setConfig] = useState<AuthConfig | null>(null);

  useEffect(() => {
    api
      .authConfig()
      .then(setConfig)
      .catch(() => setConfig({ allow_public_registration: true }));
  }, []);

  return config;
}
