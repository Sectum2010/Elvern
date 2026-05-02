import { Navigate, Outlet, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { ProviderAuthProvider } from "./auth/ProviderAuthContext";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { ShellLayout } from "./components/ShellLayout";
import { DetailPage } from "./pages/DetailPage";
import { LibraryPage } from "./pages/LibraryPage";
import { LibrarySourcePage } from "./pages/LibrarySourcePage";
import { LoginPage } from "./pages/LoginPage";
import { AdminPage } from "./pages/AdminPage";
import { AdminAssistantRequestDetailPage } from "./pages/AdminAssistantRequestDetailPage";
import { AdminAssistantRequestsPage } from "./pages/AdminAssistantRequestsPage";
import { AssistantPage } from "./pages/AssistantPage";
import { AssistantAttachmentViewerPage } from "./pages/AssistantAttachmentViewerPage";
import { InstallPage } from "./pages/DesktopPage";
import { SettingsPage } from "./pages/SettingsPage";


function ProtectedShell() {
  return (
    <ProtectedRoute>
      <ShellLayout>
        <Outlet />
      </ShellLayout>
    </ProtectedRoute>
  );
}


export default function App() {
  return (
    <AuthProvider>
      <ProviderAuthProvider>
        <div aria-hidden="true" className="app-viewport-backdrop" />
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<ProtectedShell />}>
            <Route path="/" element={<Navigate to="/library" replace />} />
            <Route path="/library" element={<LibraryPage />} />
            <Route path="/library/local" element={<LibrarySourcePage sourceKind="local" />} />
            <Route path="/library/cloud" element={<LibrarySourcePage sourceKind="cloud" />} />
            <Route path="/library/:itemId" element={<DetailPage />} />
            <Route
              path="/assistant"
              element={(
                <ProtectedRoute requireAssistant>
                  <AssistantPage />
                </ProtectedRoute>
              )}
            />
            <Route path="/attachments/:attachmentId/view" element={<AssistantAttachmentViewerPage />} />
            <Route path="/install" element={<InstallPage />} />
            <Route path="/desktop" element={<Navigate to="/install" replace />} />
            <Route
              path="/admin"
              element={(
                <ProtectedRoute requireAdmin>
                  <AdminPage />
                </ProtectedRoute>
              )}
            />
            <Route
              path="/admin/assistant"
              element={(
                <ProtectedRoute requireAdmin>
                  <AdminAssistantRequestsPage />
                </ProtectedRoute>
              )}
            />
            <Route
              path="/admin/assistant/:requestId"
              element={(
                <ProtectedRoute requireAdmin>
                  <AdminAssistantRequestDetailPage />
                </ProtectedRoute>
              )}
            />
            <Route path="/settings" element={<SettingsPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/library" replace />} />
        </Routes>
      </ProviderAuthProvider>
    </AuthProvider>
  );
}
