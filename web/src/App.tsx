import { Navigate, Route, Routes } from "react-router-dom";

import { AuthProvider, useAuth } from "@/lib/auth";
import { ConfirmProvider } from "@/lib/confirm";
import { isOrgAdmin } from "@/lib/org";
import Admin from "@/routes/Admin";
import Dashboard from "@/routes/Dashboard";
import Invite from "@/routes/Invite";
import KnowledgeBase from "@/routes/KnowledgeBase";
import LiveSession from "@/routes/LiveSession";
import Login from "@/routes/Login";
import MeetingDetail from "@/routes/MeetingDetail";
import Organization from "@/routes/Organization";
import Register from "@/routes/Register";
import Settings from "@/routes/Settings";

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();

  if (loading) return null;
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function RequireOrgAdmin({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  if (!isOrgAdmin(user)) return <Navigate to="/dashboard" replace />;
  return <>{children}</>;
}

function RequireSuperAdmin({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  if (!user?.is_super_admin) return <Navigate to="/dashboard" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <AuthProvider>
      <ConfirmProvider>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/register" element={<Register />} />
          <Route path="/invite/:token" element={<Invite />} />
          <Route
            path="/dashboard"
            element={
              <RequireAuth>
                <Dashboard />
              </RequireAuth>
            }
          />
          <Route
            path="/meetings/:meetingId"
            element={
              <RequireAuth>
                <MeetingDetail />
              </RequireAuth>
            }
          />
          <Route
            path="/meetings/:meetingId/live"
            element={
              <RequireAuth>
                <LiveSession />
              </RequireAuth>
            }
          />
          <Route
            path="/knowledge-base"
            element={
              <RequireAuth>
                <KnowledgeBase />
              </RequireAuth>
            }
          />
          <Route
            path="/settings"
            element={
              <RequireAuth>
                <Settings />
              </RequireAuth>
            }
          />
          <Route
            path="/organization"
            element={
              <RequireAuth>
                <RequireOrgAdmin>
                  <Organization />
                </RequireOrgAdmin>
              </RequireAuth>
            }
          />
          <Route
            path="/admin"
            element={
              <RequireAuth>
                <RequireSuperAdmin>
                  <Admin />
                </RequireSuperAdmin>
              </RequireAuth>
            }
          />
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </ConfirmProvider>
    </AuthProvider>
  );
}
