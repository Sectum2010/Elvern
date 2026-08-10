import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { MeridianSettingsView } from "./MeridianSettingsView.jsx";

function baseModel() {
  return {
    error: "",
    message: "",
    resourceErrors: [],
    onRetryResource: vi.fn(),
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
      onRetry: vi.fn(),
    },
    cloud: {
      isAdmin: true,
      username: "admin",
      cloudLibraries: {
        google: { enabled: true, connected: true, account_name: "Connected account" },
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

function serverModel(overrides = {}) {
  return {
    resourceStatus: {
      googleSetup: { error: "", loaded: true, loading: false },
      mediaReference: { error: "", loaded: true, loading: false },
      posterReference: { error: "", loaded: true, loading: false },
    },
    googleSetup: {
      configuration_state: "ready",
      client_secret_configured: true,
      missing_fields: [],
    },
    googleSetupDraft: {
      https_origin: "https://draft.example.test",
      client_id: "draft-client-id",
      client_secret: "",
    },
    setGoogleSetupDraft: vi.fn(),
    googleSetupSaving: false,
    googleSetupBadgeLabel: "OAuth Ready",
    googleConnectionHealth: "Connected",
    sourceHealth: "Current",
    onGoogleSetupSave: vi.fn((event) => event.preventDefault()),
    onCopyGoogleCallback: vi.fn(),
    secretInput: <input aria-label="Google OAuth client secret" />,
    sharedReference: {
      effective_value: "/srv/elvern-test/media",
      default_value: "/srv/default-media",
      category_summary: {
        movies: [{ name: "Movies" }],
        tv: [{ name: "TV Shows" }],
        cartoon: [{ name: "Cartoon" }],
        anime: [{ name: "Anime" }],
      },
      validation_rules: ["Library path rule"],
    },
    sharedReferenceInput: "/srv/elvern-test/media",
    sharedReferenceSaving: false,
    setSharedReferenceInput: vi.fn(),
    onSharedReferenceSave: vi.fn(),
    posterReference: {
      effective_value: "/srv/elvern-test/posters",
      default_value: "/srv/default-posters",
      validation_rules: ["Poster path rule"],
    },
    posterReferenceInput: "/srv/elvern-test/posters",
    posterReferenceSaving: false,
    setPosterReferenceInput: vi.fn(),
    onPosterReferenceSave: vi.fn(),
    onOpenDirectoryPicker: vi.fn(),
    onRetryGoogleSetup: vi.fn(),
    onRetryMediaReference: vi.fn(),
    onRetryPosterReference: vi.fn(),
    ...overrides,
  };
}

describe("MeridianSettingsView contracts", () => {
  test("Library keeps the dedicated age refresh control and real refresh action", () => {
    const onRefreshAgeGroups = vi.fn();
    const model = {
      ...baseModel(),
      library: {
        settings: {
          hide_recently_added: false,
          hide_duplicate_movies: false,
        },
        saving: false,
        isAdmin: true,
        ageGroupsLoading: false,
        ageBuckets: [],
        onRecentlyAddedToggle: vi.fn(),
        onDuplicateToggle: vi.fn(),
        onRefreshAgeGroups,
        onOpenAgeBucket: vi.fn(),
      },
    };
    render(<MeridianSettingsView model={model} tab="library" />);

    const refresh = screen.getByRole("button", { name: "Refresh" });
    expect(refresh).toHaveClass("meridian-age-refresh");
    fireEvent.click(refresh);
    expect(onRefreshAgeGroups).toHaveBeenCalledTimes(1);
  });

  test("Cloud connection and empty personal-library copy use the Meridian layout hooks", () => {
    const model = baseModel();
    model.cloud.cloudLibraries.my_libraries = [];
    render(<MeridianSettingsView model={model} tab="cloud-sharing" />);

    expect(screen.getByText("Connected as Connected account · cloud libraries ready to refresh.")).toBeInTheDocument();
    expect(screen.getByText("Connected")).toHaveClass("meridian-status-pill--with-dot");
    expect(screen.getByText("No personal cloud libraries added yet.")).toHaveClass("meridian-source-list__empty");
    expect(screen.getByText("Cloud libraries").closest("section")).toHaveClass("meridian-cloud-libraries-card");
  });

  test("standard users see the personal hidden scope without poster thumbnails", () => {
    const model = baseModel();
    render(<MeridianSettingsView model={model} tab="hidden-titles" />);

    const personalScope = screen.getByRole("radio", { name: "For me (1)" });
    expect(personalScope).toBeChecked();
    expect(personalScope.closest("[role='radiogroup']")).toHaveClass("meridian-hidden-scope");
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

  test("Hidden shows a stable skeleton before authoritative data and never a fake empty state", () => {
    const model = baseModel();
    model.hidden.hiddenItems = [];
    model.hidden.hiddenStatus = { error: "", loaded: false, loading: true };
    render(<MeridianSettingsView model={model} tab="hidden-titles" />);

    expect(screen.getByLabelText("Loading hidden titles")).toBeInTheDocument();
    expect(screen.queryByText("You have no hidden movies right now.")).not.toBeInTheDocument();
  });

  test("Hidden preserves cached rows during refresh and exposes a stable inline retry", () => {
    const model = baseModel();
    model.hidden.hiddenStatus = {
      error: "Hidden titles could not be refreshed.",
      loaded: true,
      loading: true,
    };
    render(<MeridianSettingsView model={model} tab="hidden-titles" />);

    expect(screen.getByText("Hidden Film")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Refreshing");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(model.hidden.onRetry).toHaveBeenCalledTimes(1);
  });

  test("Hidden destructive actions expose the real per-item pending state", () => {
    const model = baseModel();
    model.hidden.isAdmin = true;
    model.hidden.movingToGlobalItemId = 7;
    render(<MeridianSettingsView model={model} tab="hidden-titles" />);

    const pendingButton = screen.getByRole("button", { name: "Hiding…" });
    expect(pendingButton).toBeDisabled();
    fireEvent.click(pendingButton);
    expect(model.hidden.onHideForEveryone).not.toHaveBeenCalled();
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

  test("persistent resource errors stay inline and retry only their resource", () => {
    const model = baseModel();
    model.resourceErrors = [{ key: "userSettings", message: "Settings could not be loaded." }];
    render(<MeridianSettingsView model={model} tab="hidden-titles" />);

    const error = screen.getByText("Settings could not be loaded.").closest("[role='alert']");
    fireEvent.click(within(error).getByRole("button", { name: "Retry" }));
    expect(model.onRetryResource).toHaveBeenCalledWith("userSettings");
  });

  test("Server resources load independently without replacing ready cards with fake empty values", () => {
    const model = baseModel();
    model.server = serverModel({
      resourceStatus: {
        googleSetup: { error: "", loaded: false, loading: true },
        mediaReference: { error: "", loaded: true, loading: false },
        posterReference: { error: "Poster reference unavailable.", loaded: false, loading: false },
      },
    });
    render(<MeridianSettingsView model={model} tab="server-storage" />);

    expect(screen.getByLabelText("Loading Google Drive OAuth setup")).toBeInTheDocument();
    expect(screen.getByDisplayValue("/srv/elvern-test/media")).toBeInTheDocument();
    expect(screen.getByText("Poster reference unavailable.")).toBeInTheDocument();
    expect(screen.queryByText("None configured")).not.toBeInTheDocument();
  });

  test("Server OAuth wizard gates future steps and derives registration values from the unsaved draft", () => {
    const model = baseModel();
    model.server = serverModel();
    render(<MeridianSettingsView model={model} tab="server-storage" />);

    expect(screen.getByRole("button", { name: "1 · Origin" })).toHaveClass("is-active");
    fireEvent.click(screen.getByRole("button", { name: "3 · Register" }));
    expect(screen.getByText("https://draft.example.test", { exact: true })).toBeInTheDocument();
    expect(screen.getByText(
      "https://draft.example.test/api/cloud-libraries/google/callback",
      { exact: true },
    )).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(model.server.onCopyGoogleCallback).toHaveBeenCalledWith(
      "https://draft.example.test/api/cloud-libraries/google/callback",
    );
  });

  test("Server OAuth wizard rejects raw IP origins instead of unlocking future steps", () => {
    const model = baseModel();
    model.server = serverModel({
      googleSetupDraft: {
        https_origin: "https://100.64.0.10",
        client_id: "draft-client-id",
        client_secret: "draft-secret",
      },
    });
    render(<MeridianSettingsView model={model} tab="server-storage" />);

    expect(screen.getByRole("button", { name: "2 · Credentials" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "3 · Register" })).toBeDisabled();
  });

  test("Server path disclosures are closed by default and use the real rules when opened", () => {
    const model = baseModel();
    model.server = serverModel();
    render(<MeridianSettingsView model={model} tab="server-storage" />);

    expect(screen.queryByText("Library path rule")).not.toBeInTheDocument();
    expect(screen.queryByText("Poster path rule")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Path rules/ }));
    fireEvent.click(screen.getByRole("button", { name: "Accepted paths" }));
    expect(screen.getByText(/Library path rule/)).toBeInTheDocument();
    expect(screen.getByText(/Poster path rule/)).toBeInTheDocument();
  });
});
