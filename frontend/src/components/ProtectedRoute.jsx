import { Navigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { canAccessAssistant } from "../lib/assistantAccess";
import { LoadingView } from "./LoadingView";


export function ProtectedRoute({ children, requireAdmin = false, requireAssistant = false }) {
  const { user, loading } = useAuth();

  if (loading) {
    return <LoadingView label="Checking your Elvern session..." />;
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  if (requireAdmin && user.role !== "admin") {
    return <Navigate to="/library" replace />;
  }

  if (requireAssistant && !canAccessAssistant(user)) {
    return <Navigate to="/library" replace />;
  }

  return children;
}
