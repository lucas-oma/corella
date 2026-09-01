import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import AuthLayout from "@/components/AuthLayout";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useAuthConfig } from "@/lib/useAuthConfig";

export default function Login() {
  const { login } = useAuth();
  const authConfig = useAuthConfig();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password);
      navigate("/dashboard");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthLayout title="Sign in" subtitle="Your self-hosted meeting workspace">
      <form onSubmit={onSubmit} className="space-y-4">
        <div>
          <label className="label" htmlFor="email">
            Email
          </label>
          <input
            id="email"
            type="email"
            required
            className="field"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
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
            className="field"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        {error && <p className="text-sm text-status-danger">{error}</p>}
        <button type="submit" disabled={submitting} className="btn-primary w-full">
          {submitting ? "Signing in…" : "Sign in"}
        </button>
      </form>
      {authConfig?.allow_public_registration && (
        <p className="mt-5 text-center text-sm text-ink-muted">
          No account yet?{" "}
          <Link to="/register" className="text-accent dark:text-ink-inverted">
            Create one
          </Link>
        </p>
      )}
      {authConfig && !authConfig.allow_public_registration && (
        <p className="mt-5 text-center text-sm text-ink-muted">
          No account yet? Ask your admin to create one for you.
        </p>
      )}
    </AuthLayout>
  );
}
