import { Navigate, useLocation } from "react-router-dom";

import { buildLegacySourceRedirectLocation } from "../lib/desktopLibraryViewState.js";


export function LegacyLibrarySourceRedirect({ source }) {
  const location = useLocation();
  return (
    <Navigate
      replace
      state={location.state}
      to={buildLegacySourceRedirectLocation(location, source)}
    />
  );
}
