import { Link, useLocation } from "react-router-dom";

import { useAuth } from "../auth/AuthContext.jsx";
import {
  extractLibraryReturnState,
  markLibraryReturnPending,
  readLibraryReturnTarget,
} from "../lib/libraryNavigation.js";
import {
  detectClientDeviceClass,
  detectClientPlatform,
  isDesktopClientPlatform,
} from "../lib/platformDetection.js";


export function DesktopBackToLibraryButton({ className = "" }) {
  const { user } = useAuth();
  const location = useLocation();
  const desktop = detectClientDeviceClass() === "desktop"
    && isDesktopClientPlatform(detectClientPlatform());
  if (!desktop) {
    return null;
  }
  const protectedIdentity = {
    userId: user?.id,
    role: user?.role,
  };
  const returnTarget = (
    extractLibraryReturnState(location.state, protectedIdentity)
    || readLibraryReturnTarget(protectedIdentity)
  );
  const target = returnTarget?.listPath || "/library?category=movies";
  return (
    <Link
      aria-label="Back to Library"
      className={["assistant-back-button", "desktop-back-to-library-button", className]
        .filter(Boolean)
        .join(" ")}
      onClick={() => {
        if (returnTarget) {
          markLibraryReturnPending(protectedIdentity);
        }
      }}
      state={returnTarget ? { restoreLibraryReturn: true } : undefined}
      title="Back to Library"
      to={target}
    >
      &lt;
    </Link>
  );
}
