import { Navigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
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

  if (requireAssistant && !user.assistant_beta_enabled) {
    return <Navigate to="/library" replace />;
  }

  return children;
}
