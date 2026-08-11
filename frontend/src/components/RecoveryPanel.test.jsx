import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";

import { apiRequest } from "../lib/api";
import { ADMIN_BACKUP_EVENT } from "../lib/adminEvents.js";
import { RecoveryPanel } from "./RecoveryPanel.jsx";

vi.mock("../lib/api", () => ({
  apiRequest: vi.fn(),
}));

const CHECKPOINT = {
  checkpoint_id: "elvern-backup-20260810.tar.gz.enc",
  path: "backups/elvern-backup-20260810.tar.gz.enc",
  created_at_utc: "2026-08-10T12:00:00Z",
  backup_format_version: 2,
  backup_trigger: "manual_admin_ui",
  auto_checkpoint: false,
  backup_key_source: "auto",
  total_size_bytes: 4096,
  catalog_status: "valid",
};

const INTERRUPTED_JOB = {
  id: "job-interrupted",
  state: "interrupted",
  progress_percent: 37,
  message: "Interrupted",
  checkpoint_id: null,
};

function installApi({ catalogError = false, auditError = false, recentAuth = true } = {}) {
  apiRequest.mockImplementation(async (path, options = {}) => {
    if (path === "/api/admin/backups") {
      if (catalogError) throw new Error("Catalog request failed");
      return { backups_dir: "backups", checkpoints: [CHECKPOINT] };
    }
    if (path === "/api/admin/audit?limit=100") {
      if (auditError) throw new Error("Audit request failed");
      return { events: [] };
    }
    if (path === "/api/admin/backup-jobs/active") return { job: null };
    if (path === "/api/admin/backups/recent-auth/status") return { verified: recentAuth };
    if (path === "/api/admin/backups/recent-auth" && options.method === "POST") {
      return { verified: true, expires_in_seconds: 600 };
    }
    throw new Error(`Unexpected request: ${options.method || "GET"} ${path}`);
  });
}

describe("RecoveryPanel", () => {
  beforeEach(() => {
    apiRequest.mockReset();
  });

  test("renders the three real stages with Auto key as default and no restore or export action", async () => {
    installApi();
    render(<RecoveryPanel />);

    expect(await screen.findByText("1 · Create")).toBeInTheDocument();
    expect(screen.getByText("2 · Checkpoints")).toBeInTheDocument();
    expect(screen.getByText("3 · Verify & protect")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Auto key" })).toHaveClass("is-active");
    expect(screen.queryByRole("button", { name: /restore/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /export/i })).not.toBeInTheDocument();
  });

  test("keeps a catalog failure localized instead of showing a healthy count", async () => {
    installApi({ catalogError: true });
    render(<RecoveryPanel />);

    expect(await screen.findByText("Catalog request failed")).toHaveAttribute("role", "alert");
    expect(screen.getByText("Unavailable")).toBeInTheDocument();
  });

  test("reports audit warnings as unavailable instead of a false no-warning state", async () => {
    installApi({ auditError: true });
    render(<RecoveryPanel />);

    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith("/api/admin/backups"));
    fireEvent.click(screen.getByRole("button", { name: "2 · Checkpoints" }));
    fireEvent.click(await screen.findByRole("button", { name: /Manual · Admin UI/i }));
    fireEvent.click(screen.getByRole("button", { name: "3 · Verify & protect" }));
    expect(screen.getByText("Backup warning status unavailable.")).toBeInTheDocument();
    expect(screen.queryByText(/No recent backup warnings/)).not.toBeInTheDocument();
  });

  test("recent-auth dialog traps focus, clears the password on Escape, and returns focus", async () => {
    installApi({ recentAuth: false });
    render(<RecoveryPanel />);
    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith("/api/admin/backups"));

    const trigger = screen.getByRole("button", { name: "Create encrypted backup" });
    trigger.focus();
    fireEvent.click(trigger);
    const dialog = await screen.findByRole("dialog", { name: "Confirm your password" });
    const password = within(dialog).getByPlaceholderText("Current admin password");
    expect(password).toHaveFocus();
    fireEvent.change(password, { target: { value: "temporary-secret" } });
    fireEvent.keyDown(document, { key: "Escape" });

    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Confirm your password" })).not.toBeInTheDocument());
    expect(trigger).toHaveFocus();
    fireEvent.click(trigger);
    expect(await screen.findByPlaceholderText("Current admin password")).toHaveValue("");
  });

  test("uses one idempotency key for a rapid create intent and reuses it after an uncertain network failure", async () => {
    let createAttempts = 0;
    const createPayloads = [];
    apiRequest.mockImplementation(async (path, options = {}) => {
      if (path === "/api/admin/backups") return { backups_dir: "backups", checkpoints: [CHECKPOINT] };
      if (path === "/api/admin/audit?limit=100") return { events: [] };
      if (path === "/api/admin/backup-jobs/active") return { job: null };
      if (path === "/api/admin/backups/recent-auth/status") return { verified: true };
      if (path === "/api/admin/backup-jobs" && options.method === "POST") {
        createAttempts += 1;
        createPayloads.push(options.data);
        if (createAttempts === 1) throw new Error("Network response was lost");
        return INTERRUPTED_JOB;
      }
      throw new Error(`Unexpected request: ${options.method || "GET"} ${path}`);
    });
    render(<RecoveryPanel />);
    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith("/api/admin/backups"));

    const create = screen.getByRole("button", { name: "Create encrypted backup" });
    fireEvent.click(create);
    fireEvent.click(create);
    expect(await screen.findByText("Network response was lost")).toBeInTheDocument();
    expect(createAttempts).toBe(1);

    fireEvent.click(create);
    await waitFor(() => expect(createAttempts).toBe(2));
    expect(createPayloads[0].idempotency_key).toBeTruthy();
    expect(createPayloads[1].idempotency_key).toBe(createPayloads[0].idempotency_key);
    expect(await screen.findByText("Interrupted")).toBeInTheDocument();
  });

  test("reacts to backup SSE notifications while keeping active-job polling as fallback", async () => {
    let activeRequests = 0;
    apiRequest.mockImplementation(async (path) => {
      if (path === "/api/admin/backups") return { backups_dir: "backups", checkpoints: [CHECKPOINT] };
      if (path === "/api/admin/audit?limit=100") return { events: [] };
      if (path === "/api/admin/backup-jobs/active") {
        activeRequests += 1;
        return { job: activeRequests === 1 ? null : INTERRUPTED_JOB };
      }
      throw new Error(`Unexpected request: GET ${path}`);
    });
    render(<RecoveryPanel />);
    await waitFor(() => expect(activeRequests).toBe(1));

    window.dispatchEvent(new CustomEvent(ADMIN_BACKUP_EVENT, {
      detail: { eventType: "backup_job_updated" },
    }));

    expect(await screen.findByText("Interrupted")).toBeInTheDocument();
    expect(activeRequests).toBe(2);
  });
});
