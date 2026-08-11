import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, test, vi } from "vitest";

import { UserActionsDialog } from "./UserActionsDialog.jsx";

const USER = {
  username: "meridian-user",
  enabled: true,
  status_label: "Active",
  status_color: "#2e9f6f",
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
});
