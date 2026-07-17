import { useEffect } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { canonicalizeBrowserLocation } from "../lib/canonicalSpaPath.js";


export function CanonicalSpaRouteGuard({ children }) {
  const location = useLocation();
  const navigate = useNavigate();
  const canonical = canonicalizeBrowserLocation(location);

  useEffect(() => {
    if (!canonical.changed) {
      return;
    }
    navigate(canonical.href, {
      replace: true,
      state: location.state,
    });
  }, [canonical.changed, canonical.href, location.state, navigate]);

  return canonical.changed ? null : children;
}
