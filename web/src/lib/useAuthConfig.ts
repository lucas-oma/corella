import { useEffect, useState } from "react";

import { api, type AuthConfig } from "@/lib/api";

/** Instance-wide auth config (self-serve registration, org cap, whether
 * invite emails are enabled). `null` while loading. Defaults open on fetch
 * failure so a misconfigured/unreachable API doesn't strand people on a
 * blank screen.
 */
export function useAuthConfig(): AuthConfig | null {
  const [config, setConfig] = useState<AuthConfig | null>(null);

  useEffect(() => {
    api
      .authConfig()
      .then(setConfig)
      .catch(() =>
        setConfig({ allow_public_registration: true, max_orgs_per_user: 1, email_invites: false }),
      );
  }, []);

  return config;
}
