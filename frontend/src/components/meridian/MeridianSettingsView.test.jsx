import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { MeridianSettingsView } from "./MeridianSettingsView.jsx";

function baseModel() {
  return {
    error: "",
    message: "",
    resourceErrors: [],
    hidden: {
      isAdmin: false,
      hiddenItems: [{
        id: 7,
        title: "Hidden Film",
        year: 2026,
        edition_label: "Director's Cut",
        poster_url: "/api/posters/private.jpg",
      }],
      globalHiddenItems: [],
      hiddenLoading: false,
      hiddenExpanded: { personal: false, global: false },
      onHiddenExpandedChange: vi.fn(),
      onShowAgain: vi.fn(),
      onShowForEveryone: vi.fn(),
      onHideForEveryone: vi.fn(),
      onHideForMe: vi.fn(),
    },
    cloud: {
      isAdmin: true,
      username: "admin",
      cloudLibraries: {
        google: { connected: true, account_name: "Connected account" },
        my_libraries: [{
          id: 11,
          display_name: "Personal source",
          item_count: 3,
          resource_type: "folder",
          last_synced_at: null,
        }],
        shared_libraries: [{
          id: 12,
          display_name: "Shared source",
          item_count: 4,
          resource_type: "shared_drive",
          owner_username: "admin",
          hidden_for_user: false,
          last_synced_at: null,
        }],
      },
      cloudBusyKey: "",
      myLibraryDraft: { resource_type: "folder", resource_id: "" },
      sharedLibraryDraft: { resource_type: "folder", resource_id: "" },
      setMyLibraryDraft: vi.fn(),
      setSharedLibraryDraft: vi.fn(),
      onGoogleConnect: vi.fn(),
      onAddCloudSource: vi.fn(),
      onMoveCloudSource: vi.fn(),
      onSharedVisibilityToggle: vi.fn(),
      formatCloudTimestamp: () => "Never",
    },
  };
}

describe("MeridianSettingsView contracts", () => {
  test("standard users see the personal hidden scope without poster thumbnails", () => {
    const model = baseModel();
    render(<MeridianSettingsView model={model} tab="hidden-titles" />);

    expect(screen.getByRole("radio", { name: "For me (1)" })).toBeChecked();
    expect(screen.queryByRole("radio", { name: /For everyone/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
    expect(screen.getByText("H")).toBeInTheDocument();
    expect(screen.getByText("2026 · Director's Cut")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Hide universally" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Hide for everyone" })).not.toBeInTheDocument();
  });

  test("admin hidden scopes use the approved transfer actions", () => {
    const model = baseModel();
    model.hidden.isAdmin = true;
    model.hidden.globalHiddenItems = [{ id: 8, title: "Global Film" }];
    render(<MeridianSettingsView model={model} tab="hidden-titles" />);

    fireEvent.click(screen.getByRole("button", { name: "Hide for everyone" }));
    expect(model.hidden.onHideForEveryone).toHaveBeenCalledWith(model.hidden.hiddenItems[0]);

    fireEvent.click(screen.getByRole("radio", { name: "For everyone (1)" }));
    fireEvent.click(screen.getByRole("button", { name: "Hide for me" }));
    expect(model.hidden.onHideForMe).toHaveBeenCalledWith(model.hidden.globalHiddenItems[0]);
    expect(screen.queryByText("Hide universally")).not.toBeInTheDocument();
  });

  test("cloud source actions preserve personal and shared endpoint boundaries", () => {
    const model = baseModel();
    render(<MeridianSettingsView model={model} tab="cloud-sharing" />);

    const personal = screen.getByText("Personal source").closest("article");
    const shared = screen.getByText("Shared source").closest("article");
    expect(within(personal).queryByRole("button", { name: /Hide for me/ })).not.toBeInTheDocument();

    fireEvent.click(within(personal).getByRole("button", { name: "Share globally" }));
    expect(model.cloud.onMoveCloudSource).toHaveBeenCalledWith(
      expect.objectContaining({ id: model.cloud.cloudLibraries.my_libraries[0].id }),
      true,
    );

    fireEvent.click(within(shared).getByRole("button", { name: "Move to My Libraries" }));
    expect(model.cloud.onMoveCloudSource).toHaveBeenCalledWith(
      expect.objectContaining({ id: model.cloud.cloudLibraries.shared_libraries[0].id }),
      false,
    );
    fireEvent.click(within(shared).getByRole("button", { name: "Hide for me" }));
    expect(model.cloud.onSharedVisibilityToggle).toHaveBeenCalledWith(
      expect.objectContaining({ id: model.cloud.cloudLibraries.shared_libraries[0].id }),
    );
  });
});
