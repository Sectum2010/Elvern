import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation, useNavigationType } from "react-router-dom";
import { afterEach, describe, expect, test } from "vitest";

import { CanonicalSpaRouteGuard } from "./CanonicalSpaRouteGuard.jsx";


function LocationProbe() {
  const location = useLocation();
  const navigationType = useNavigationType();
  return <p>{`${location.pathname}${location.search}${location.hash}|${navigationType}|${location.state?.marker || ""}`}</p>;
}


describe("CanonicalSpaRouteGuard", () => {
  afterEach(cleanup);

  test("replaces a client-side trailing slash before rendering route content", async () => {
    render(
      <MemoryRouter initialEntries={[{
        pathname: "/library/",
        search: "?category=anime",
        hash: "#rail",
        state: { marker: "preserved" },
      }]}>
        <LocationProbe />
        <CanonicalSpaRouteGuard>
          <p>Canonical content</p>
        </CanonicalSpaRouteGuard>
      </MemoryRouter>,
    );

    await waitFor(() => expect(screen.getByText(
      "/library?category=anime#rail|REPLACE|preserved",
    )).toBeInTheDocument());
    expect(screen.getByText("Canonical content")).toBeInTheDocument();
  });
});
