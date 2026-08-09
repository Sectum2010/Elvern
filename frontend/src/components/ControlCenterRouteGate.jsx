import { Navigate, Outlet, useLocation } from "react-router-dom";

import { useAuth } from "../auth/AuthContext.jsx";
import { detectClientDeviceClass, detectClientPlatform } from "../lib/platformDetection.js";
import {
  classifyControlCenterPath,
  isDesktopControlCenterDevice,
  resolveControlCenterLocation,
} from "../lib/controlCenterRoutes.js";
import { ControlCenterSessionProvider } from "./ControlCenterSessionContext.jsx";
import { DesktopControlCenterLayout } from "./DesktopControlCenterLayout.jsx";

export function ControlCenterRouteGate() {
  const { user } = useAuth();
  const location = useLocation();
  const desktopControlCenter = isDesktopControlCenterDevice(
    detectClientDeviceClass(),
    detectClientPlatform(),
  );

  if (!desktopControlCenter) {
    return <Outlet />;
  }

  const classification = classifyControlCenterPath(location.pathname);
  if (classification.area === "admin" && user?.role !== "admin") {
    return <Navigate replace state={location.state} to="/library" />;
  }

  const redirect = resolveControlCenterLocation({
    pathname: location.pathname,
    search: location.search,
    hash: location.hash,
    role: user?.role,
  });
  if (redirect) {
    return <Navigate replace state={location.state} to={redirect} />;
  }

  return (
    <ControlCenterSessionProvider>
      <DesktopControlCenterLayout />
    </ControlCenterSessionProvider>
  );
}
