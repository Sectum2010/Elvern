import { QueryClientProvider } from "@tanstack/react-query";
import { Navigate, Outlet, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { ProviderAuthProvider } from "./auth/ProviderAuthContext";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { CanonicalSpaRouteGuard } from "./components/CanonicalSpaRouteGuard";
import { LegacyInstallRedirect } from "./components/LegacyInstallRedirect";
import { LegacyLibrarySourceRedirect } from "./components/LegacyLibrarySourceRedirect";
import { ShellLayout } from "./components/ShellLayout";
import { StartupConnectionGate } from "./components/StartupConnectionGate";
import { DetailPage } from "./pages/DetailPage";
import { LibraryPage } from "./pages/LibraryPage";
import { LoginPage } from "./pages/LoginPage";
import { NewUserPage } from "./pages/NewUserPage";
import { ForgotPasswordPage } from "./pages/ForgotPasswordPage";
import { TotpChallengePage } from "./pages/TotpChallengePage";
import { TotpSetupPage } from "./pages/TotpSetupPage";
import { AdminPage } from "./pages/AdminPage";
import { AdminAssistantRequestDetailPage } from "./pages/AdminAssistantRequestDetailPage";
import { AdminAssistantRequestsPage } from "./pages/AdminAssistantRequestsPage";
import { AssistantPage } from "./pages/AssistantPage";
import { AssistantAttachmentViewerPage } from "./pages/AssistantAttachmentViewerPage";
import { SettingsPage } from "./pages/SettingsPage";
import { queryClient } from "./lib/queryClient";
import { LibraryRevisionSynchronizer } from "./lib/libraryRevisionQueries.js";


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
    <QueryClientProvider client={queryClient}>
      <StartupConnectionGate>
        <AuthProvider>
          <LibraryRevisionSynchronizer />
          <ProviderAuthProvider>
            <CanonicalSpaRouteGuard>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/login/totp" element={<TotpChallengePage />} />
            <Route path="/new-user" element={<NewUserPage />} />
            <Route path="/forgot-password" element={<ForgotPasswordPage />} />
            <Route
              path="/install"
              element={(
                <ProtectedRoute>
                  <LegacyInstallRedirect />
                </ProtectedRoute>
              )}
            />
            <Route
              path="/desktop"
              element={(
                <ProtectedRoute>
                  <LegacyInstallRedirect />
                </ProtectedRoute>
              )}
            />
            <Route element={<ProtectedShell />}>
              <Route path="/setup/totp" element={<TotpSetupPage />} />
              <Route path="/" element={<Navigate to="/library" replace />} />
              <Route path="/library" element={<LibraryPage />} />
              <Route path="/library/local" element={<LegacyLibrarySourceRedirect source="local" />} />
              <Route path="/library/cloud" element={<LegacyLibrarySourceRedirect source="cloud" />} />
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
            </CanonicalSpaRouteGuard>
          </ProviderAuthProvider>
        </AuthProvider>
      </StartupConnectionGate>
    </QueryClientProvider>
  );
}
