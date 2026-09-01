import { Navigate, Route, Routes } from "react-router-dom";

import { AuthProvider, useAuth } from "@/lib/auth";
import Dashboard from "@/routes/Dashboard";
import KnowledgeBase from "@/routes/KnowledgeBase";
import LiveSession from "@/routes/LiveSession";
import Login from "@/routes/Login";
import MeetingDetail from "@/routes/MeetingDetail";
import Register from "@/routes/Register";
import Settings from "@/routes/Settings";

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();

  if (loading) return null;
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<Register />} />
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
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </AuthProvider>
  );
}
