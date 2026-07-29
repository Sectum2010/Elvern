import { render, screen } from "@testing-library/react";
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useNavigationType,
} from "react-router-dom";
import { describe, expect, test } from "vitest";

import { LegacyLibrarySourceRedirect } from "./LegacyLibrarySourceRedirect.jsx";


function LocationProbe() {
  const location = useLocation();
  const navigationType = useNavigationType();
  return (
    <output data-testid="location">
      {`${location.pathname}${location.search}${location.hash}|${navigationType}|${location.state?.token || ""}`}
    </output>
  );
}


function renderRedirect(initialEntry, source) {
  render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route
          path={`/library/${source}`}
          element={<LegacyLibrarySourceRedirect source={source} />}
        />
        <Route path="/library" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}


describe("LegacyLibrarySourceRedirect", () => {
  test.each([
    [
      {
        pathname: "/library/local",
        search: "?q=akira&category=anime",
        hash: "#poster",
        state: { token: "preserved" },
      },
      "local",
      "/library?q=akira&category=anime&source=local#poster|REPLACE|preserved",
    ],
    [
      {
        pathname: "/library/cloud",
        search: "?source=local&quality=gold",
        state: { token: "preserved" },
      },
      "cloud",
      "/library?source=cloud&quality=gold|REPLACE|preserved",
    ],
  ])("canonicalizes the legacy %s source route without losing state", (entry, source, expected) => {
    renderRedirect(entry, source);

    expect(screen.getByTestId("location")).toHaveTextContent(expected);
  });
});
