import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";

import AuthLayout from "@/components/AuthLayout";
import { ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useAuthConfig } from "@/lib/useAuthConfig";

export default function Register() {
  const { register } = useAuth();
  const authConfig = useAuthConfig();
  const navigate = useNavigate();
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await register(email, password, fullName);
      navigate("/dashboard");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong");
    } finally {
      setSubmitting(false);
    }
  }

  if (authConfig && !authConfig.allow_public_registration) {
    return (
      <AuthLayout title="Registration is closed" subtitle="Your self-hosted meeting workspace">
        <p className="text-sm text-ink-muted">
          This instance is admin-managed. Ask your admin to create an account for you, then{" "}
          <Link to="/login" className="text-accent dark:text-ink-inverted">
            sign in
          </Link>
          .
        </p>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout title="Create your account" subtitle="Your self-hosted meeting workspace">
      <form onSubmit={onSubmit} className="space-y-4">
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
            minLength={8}
            className="field"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        {error && <p className="text-sm text-status-danger">{error}</p>}
        <button type="submit" disabled={submitting} className="btn-primary w-full">
          {submitting ? "Creating account…" : "Create account"}
        </button>
      </form>
      <p className="mt-5 text-center text-sm text-ink-muted">
        Already have an account?{" "}
        <Link to="/login" className="text-accent dark:text-ink-inverted">
          Sign in
        </Link>
      </p>
    </AuthLayout>
  );
}
