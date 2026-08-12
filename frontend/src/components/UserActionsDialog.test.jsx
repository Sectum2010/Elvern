import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import {
  MeridianUserActionsAccountTab,
  MeridianUserActionsAssistantTab,
  MeridianUserActionsDownloadsTab,
  UserActionsDialog,
} from "./UserActionsDialog.jsx";

const USER = {
  username: "meridian-user",
  enabled: true,
  status_label: "Active",
  status_color: "green",
  active_sessions: 1,
  last_login_label: "today",
};

function renderDialog(overrides = {}) {
  const onRequestClose = vi.fn();
  const onTabChange = vi.fn();
  const returnFocusElement = document.createElement("button");
  document.body.append(returnFocusElement);
  return {
    onRequestClose,
    onTabChange,
    returnFocusElement,
    ...render(
      <UserActionsDialog
        activeTab="account"
        onRequestClose={onRequestClose}
        onTabChange={onTabChange}
        returnFocusElement={returnFocusElement}
        user={USER}
        {...overrides}
      >
        <button type="button">Panel action</button>
      </UserActionsDialog>,
    ),
  };
}

describe("UserActionsDialog", () => {
  test("uses the exact Account, Assistant, and Downloads tabs and defaults to Account", () => {
    const { onTabChange } = renderDialog();
    const tabs = screen.getByRole("tablist", { name: "User action sections" });

    expect(within(tabs).getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "Account",
      "Assistant",
      "Downloads",
    ]);
    expect(within(tabs).getByRole("tab", { name: "Account" })).toHaveAttribute("aria-selected", "true");
    fireEvent.click(within(tabs).getByRole("tab", { name: "Downloads" }));
    expect(onTabChange).toHaveBeenCalledWith("downloads");
  });

  test("keeps the demo blue avatar and supports Escape close with focus return", () => {
    const { onRequestClose, returnFocusElement, unmount } = renderDialog();
    const dialog = screen.getByRole("dialog", { name: "meridian-user" });

    expect(dialog.querySelector(".meridian-user-actions__avatar")).toHaveTextContent("ME");
    expect(dialog.querySelector(".meridian-user-actions__avatar")).toHaveClass("meridian-user-actions__avatar");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onRequestClose).toHaveBeenCalledTimes(1);
    unmount();
    expect(returnFocusElement).toHaveFocus();
    returnFocusElement.remove();
  });

  test("traps Tab focus inside the dialog", () => {
    renderDialog();
    const dialog = screen.getByRole("dialog", { name: "meridian-user" });
    const focusable = within(dialog).getAllByRole("button");
    const first = focusable[0];
    const last = focusable.at(-1);

    last.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(first).toHaveFocus();
    first.focus();
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(last).toHaveFocus();
  });

  test("keeps login activity and heartbeat on one optional metadata line", () => {
    const { rerender } = renderDialog({
      user: {
        ...USER,
        last_login_at: "2026-08-11T10:00:00Z",
        last_login_label: "today",
        last_activity_at: "2026-08-11T10:01:00Z",
        last_activity_label: "one minute ago",
        last_seen_at: "2026-08-11T10:02:00Z",
        last_heartbeat_label: "just now",
      },
    });
    expect(document.querySelectorAll(".meridian-user-actions__last-login")).toHaveLength(1);
    expect(document.querySelector(".meridian-user-actions__last-login")).toHaveTextContent(
      "Last login today · Last activity one minute ago · Last heartbeat just now",
    );

    rerender(
      <UserActionsDialog
        activeTab="account"
        onRequestClose={() => {}}
        onTabChange={() => {}}
        user={{ ...USER, last_login_at: null, last_activity_at: null, last_seen_at: null }}
      >
        <button type="button">Panel action</button>
      </UserActionsDialog>,
    );
    expect(document.querySelector(".meridian-user-actions__last-login")).toBeNull();
  });

  test("dedicated Meridian tab components own their inner controls", () => {
    const { rerender } = render(
      <MeridianUserActionsAccountTab
        actionItems={[{ key: "password", label: "Update password", onClick: () => {} }]}
        ageCredential={18}
        ageOptions={[18, 17, 16]}
        formatAgeCredential={(age) => (age === 18 ? "18+" : String(age))}
        onAgeChange={() => {}}
        onSaveAge={() => {}}
        onToggleAllAges={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: "Update password" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "18+" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "16" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save age credential" })).toBeInTheDocument();

    rerender(
      <MeridianUserActionsAssistantTab
        enabled
        isStandardUser
        onToggle={() => {}}
      />,
    );
    expect(screen.getByText("Enabled")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Disable Assistant" })).toBeInTheDocument();

    rerender(
      <MeridianUserActionsDownloadsTab
        accessState={{
          accessMode: "none",
          error: "",
          feedback: "",
          loading: false,
          saving: false,
          searchPending: false,
          searchQuery: "",
          searchResults: [],
          selectedItems: [],
        }}
        dirty={false}
        formatBytes={() => "0 B"}
        isAdmin={false}
        onAddMovie={() => {}}
        onModeChange={() => {}}
        onRemoveMovie={() => {}}
        onSave={() => {}}
        onSearchChange={() => {}}
      />,
    );
    expect(screen.getByText("BETA")).toBeInTheDocument();
    expect(screen.getByText("No download access")).toBeInTheDocument();
    expect(screen.getByText("Enable access to all movies")).toBeInTheDocument();
    expect(screen.getByText("Select available movies")).toBeInTheDocument();
  });
});
