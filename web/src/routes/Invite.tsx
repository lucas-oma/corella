import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import AuthLayout from "@/components/AuthLayout";
import { ApiError, api, setToken, type InvitePreview } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function Invite() {
  const { token } = useParams<{ token: string }>();
  const { user, loading, refreshUser } = useAuth();
  const navigate = useNavigate();
  const [preview, setPreview] = useState<InvitePreview | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [needsLogin, setNeedsLogin] = useState(false);

  useEffect(() => {
    if (!token) return;
    api
      .previewInvite(token)
      .then(setPreview)
      .catch((err) => {
        setLoadError(err instanceof ApiError ? err.message : "This invite is not valid");
      });
  }, [token]);

  async function accept(payload: { password?: string; full_name?: string } = {}) {
    if (!token) return;
    setError(null);
    setSubmitting(true);
    try {
      const result = await api.acceptInvite(token, payload);
      setToken(result.access_token);
      await refreshUser();
      navigate("/dashboard");
    } catch (err) {
      if (err instanceof ApiError && err.status === 409 && !user) {
        setNeedsLogin(true);
        setError(err.message);
      } else {
        setError(err instanceof ApiError ? err.message : "Couldn't accept invite");
      }
    } finally {
      setSubmitting(false);
    }
  }

  async function onRegister(e: FormEvent) {
    e.preventDefault();
    await accept({ password, full_name: fullName });
  }

  if (loadError) {
    return (
      <AuthLayout title="Invite unavailable" subtitle="Your self-hosted meeting workspace">
        <p className="text-sm text-ink-muted">{loadError}</p>
        <p className="mt-5 text-center text-sm text-ink-muted">
          <Link to="/login" className="text-accent dark:text-ink-inverted">
            Sign in
          </Link>
        </p>
      </AuthLayout>
    );
  }

  if (!preview || loading) {
    return (
      <AuthLayout title="Invite" subtitle="Your self-hosted meeting workspace">
        <p className="text-sm text-ink-muted">Loading…</p>
      </AuthLayout>
    );
  }

  const emailMismatch = Boolean(user && user.email !== preview.email);
  const next = `/invite/${token}`;

  return (
    <AuthLayout
      title={`Join ${preview.organization_name}`}
      subtitle={`Invited as ${preview.role} · ${preview.email}`}
    >
      {emailMismatch && (
        <p className="text-sm text-ink-muted">
          This invite was sent to {preview.email}. You&apos;re signed in as {user?.email}. Sign out
          and open the link again, or ask for an invite to your address.
        </p>
      )}

      {!emailMismatch && user && (
        <>
          <p className="text-sm text-ink-muted">
            You&apos;re signed in as {user.email}. Accept to join this organization.
          </p>
          {error && <p className="mt-4 text-sm text-status-danger">{error}</p>}
          <button
            type="button"
            disabled={submitting}
            onClick={() => void accept()}
            className="btn-primary mt-5 w-full"
          >
            {submitting ? "Joining…" : "Accept invite"}
          </button>
        </>
      )}

      {!user && needsLogin && (
        <>
          <p className="text-sm text-ink-muted">{error}</p>
          <p className="mt-5 text-center text-sm text-ink-muted">
            <Link
              to={`/login?next=${encodeURIComponent(next)}`}
              className="text-accent dark:text-ink-inverted"
            >
              Sign in to accept
            </Link>
          </p>
        </>
      )}

      {!user && !needsLogin && (
        <form onSubmit={onRegister} className="space-y-4">
          <p className="text-sm text-ink-muted">
            Create an account to join. If you already have one,{" "}
            <Link
              to={`/login?next=${encodeURIComponent(next)}`}
              className="text-accent dark:text-ink-inverted"
            >
              sign in
            </Link>{" "}
            first, then open this link again.
          </p>
          <div>
            <label className="label" htmlFor="fullName">
              Full name
            </label>
            <input
              id="fullName"
              required
              className="field"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
            />
          </div>
          <div>
            <label className="label" htmlFor="password">
              Password
            </label>
            <input
              id="password"
              type="password"
              required
              minLength={8}
              className="field"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          {error && <p className="text-sm text-status-danger">{error}</p>}
          <button type="submit" disabled={submitting} className="btn-primary w-full">
            {submitting ? "Joining…" : "Create account and join"}
          </button>
        </form>
      )}
    </AuthLayout>
  );
}
