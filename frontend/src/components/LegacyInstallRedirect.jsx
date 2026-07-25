import { Navigate, useLocation } from "react-router-dom";

import { buildSettingsSectionLocation } from "../lib/settingsSectionState.js";


export function LegacyInstallRedirect() {
  const location = useLocation();
  return (
    <Navigate
      replace
      state={location.state}
      to={buildSettingsSectionLocation(
        {
          pathname: "/settings",
          search: location.search,
          hash: location.hash,
        },
        "install",
      )}
    />
  );
}
