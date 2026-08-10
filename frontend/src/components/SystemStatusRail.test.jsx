import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { apiRequest } from "../lib/api.js";
import { queryClient } from "../lib/queryClient.js";
import {
  buildSystemStatusRailRows,
  SYSTEM_STATUS_RAIL_REFRESH_MS,
  SystemStatusRail,
} from "./SystemStatusRail.jsx";

const authState = { user: { id: 1, role: "admin" } };
const sessionState = {
  statusRailOpen: true,
  setStatusRailOpen: vi.fn(),
};

vi.mock("../auth/AuthContext.jsx", () => ({ useAuth: () => authState }));
vi.mock("./ControlCenterSessionContext.jsx", () => ({
  useControlCenterSession: () => sessionState,
}));
vi.mock("../lib/api.js", () => ({ apiRequest: vi.fn() }));
vi.mock("../lib/device.js", () => ({ getOrCreateDeviceId: () => "device-test" }));
vi.mock("../lib/platformDetection.js", () => ({ detectClientPlatform: () => "linux" }));

function responseFor(path) {
  if (path === "/api/system/status") return { total_media_items: 92 };
  if (path === "/api/cloud-libraries") return { google: { enabled: true, connected: true } };
  if (path === "/api/admin/google-drive-setup") return { configuration_label: "Configured" };
  if (path === "/api/user-hidden-items") return { items: [{ id: 1 }] };
  if (path === "/api/admin/global-hidden-items") return { items: [{ id: 2 }, { id: 3 }] };
  if (path === "/api/user-settings") {
    return { poster_card_display_max_width: "1400", desktop_floating_island_position: "top" };
  }
  if (path.startsWith("/api/desktop-helper/status?")) {
    return {
      helper_required: false,
      same_host: true,
      vlc_detection_state: "installed",
      last_seen_helper_at: null,
    };
  }
  throw new Error(`Unexpected request: ${path}`);
}

beforeEach(() => {
  authState.user = { id: 1, role: "admin" };
  sessionState.statusRailOpen = true;
  sessionState.setStatusRailOpen.mockClear();
  apiRequest.mockImplementation((path) => Promise.resolve(responseFor(path)));
});

afterEach(() => {
  queryClient.clear();
  vi.clearAllMocks();
});

describe("SystemStatusRail", () => {
  test("uses the approved visible-only refresh interval", () => {
    expect(SYSTEM_STATUS_RAIL_REFRESH_MS).toBe(30_000);
  });

  test("builds real rows without converting missing payloads to zero", () => {
    const rows = Object.fromEntries(buildSystemStatusRailRows({
      platform: "mac",
      deviceId: "device-a",
      payloads: {
        system: { total_media_items: 0 },
        personalHidden: { items: [] },
        globalHidden: { items: [{ id: 2 }] },
        desktopHelper: { helper_required: true, vlc_detection_state: "detection_unavailable" },
      },
    }));
    expect(rows["Titles indexed"]).toBe("0");
    expect(rows["Hidden titles"]).toBe("1");
    expect(rows["Google Drive"]).toBe("Unavailable");
  });

  test("loads only when an admin opens it and closes with Escape", async () => {
    render(<SystemStatusRail />);
    expect(await screen.findByText("92")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.getByText("VLC on host")).toBeInTheDocument();
    expect(screen.getAllByText("Not required")).toHaveLength(2);
    expect(apiRequest).toHaveBeenCalledTimes(7);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(sessionState.setStatusRailOpen).toHaveBeenCalledWith(false);
  });

  test("stays mounted while closed and performs zero protected status requests", async () => {
    sessionState.statusRailOpen = false;
    const { container } = render(<SystemStatusRail />);

    const rail = container.querySelector(".control-center-status-rail");
    expect(rail).toBeInTheDocument();
    expect(rail).toHaveAttribute("aria-hidden", "true");
    await waitFor(() => expect(apiRequest).not.toHaveBeenCalled());
  });

  test("uses real tone classes and keeps the complete device identifier", async () => {
    const { container } = render(<SystemStatusRail />);
    expect(await screen.findByText("92")).toBeInTheDocument();

    expect(screen.getByText("Google Drive").closest("dt").querySelector("i")).toHaveClass("is-success");
    expect(screen.getByText("Hidden titles").closest("dt").querySelector("i")).toHaveClass("is-hidden");
    expect(container.querySelector(".control-center-status-rail__device"))
      .toHaveTextContent("device-test");
  });

  test("uses the demo row order and keeps the Device heading outside its value card", async () => {
    const { container } = render(<SystemStatusRail />);
    expect(await screen.findByText("92")).toBeInTheDocument();

    const googleRow = screen.getByText("Google Drive").closest("div");
    expect(googleRow.children).toHaveLength(2);
    expect(googleRow.firstElementChild.tagName).toBe("DT");
    expect(googleRow.firstElementChild.querySelector("i")).toBeInTheDocument();
    expect(googleRow.lastElementChild.tagName).toBe("DD");

    const deviceSection = container.querySelector(".control-center-status-rail__device");
    const deviceCard = container.querySelector(".control-center-status-rail__device-card");
    expect(deviceSection.firstElementChild).toHaveTextContent("DEVICE");
    expect(deviceCard).not.toContainElement(deviceSection.firstElementChild);
    expect(deviceCard.querySelector(".control-center-status-rail__device-id"))
      .toHaveTextContent("device-test");
  });

  test("reconnect-required Google state takes priority over a stale connected flag", () => {
    const rows = Object.fromEntries(buildSystemStatusRailRows({
      platform: "linux",
      deviceId: "device-test",
      payloads: {
        cloudLibraries: {
          google: { enabled: true, connected: true, reconnect_required: true },
        },
      },
    }));
    expect(rows["Google Drive"]).toBe("Needs attention");
  });

  test("does not request protected status for a standard user", async () => {
    authState.user = { id: 2, role: "standard" };
    const { container } = render(<SystemStatusRail />);
    expect(container).toBeEmptyDOMElement();
    await waitFor(() => expect(apiRequest).not.toHaveBeenCalled());
  });

  test("preserves last known values and labels failed refreshes stale", async () => {
    render(<SystemStatusRail />);
    expect(await screen.findByText("92")).toBeInTheDocument();
    apiRequest.mockRejectedValue(new Error("offline"));
    fireEvent(document, new Event("visibilitychange"));
    expect((await screen.findAllByLabelText("Last known value")).length).toBeGreaterThan(0);
    expect(screen.getByText("92")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Refresh system status" })).not.toBeInTheDocument();
  });
});
