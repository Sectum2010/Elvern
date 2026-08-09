import { Navigate, useLocation } from "react-router-dom";

export function LegacyInstallRedirect() {
  const location = useLocation();
  const params = new URLSearchParams(location.search);
  params.delete("section");
  const search = params.toString();
  return (
    <Navigate
      replace
      state={location.state}
      to={`/settings/playback-apps${search ? `?${search}` : ""}${location.hash || ""}`}
    />
  );
}
