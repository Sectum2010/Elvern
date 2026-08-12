import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

function installApi({ catalogError = false, auditError = false, recentAuth = true, recentAuthError = false } = {}) {
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
    if (path === "/api/admin/backups/recent-auth/status") {
      if (recentAuthError) throw new Error("Recent authentication unavailable");
      return { verified: recentAuth };
    }
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
    expect(document.querySelectorAll(".meridian-recovery__card")).toHaveLength(1);
    expect(screen.getByText(/independent backup keyring/i)).toBeInTheDocument();
    expect(screen.queryByText(/session.secret/i)).not.toBeInTheDocument();
  });

  test("distinguishes an empty catalog from an unavailable catalog", async () => {
    apiRequest.mockImplementation(async (path) => {
      if (path === "/api/admin/backups") return { backups_dir: "backups", checkpoints: [] };
      if (path === "/api/admin/audit?limit=100") return { events: [] };
      if (path === "/api/admin/backup-jobs/active") return { job: null };
      throw new Error(`Unexpected request: GET ${path}`);
    });
    const { unmount } = render(<RecoveryPanel />);

    expect(await screen.findByText("0")).toBeInTheDocument();
    expect(screen.getByText("· No checkpoint yet")).toBeInTheDocument();
    unmount();

    installApi({ catalogError: true });
    render(<RecoveryPanel />);
    expect(await screen.findByText("Unavailable")).toBeInTheDocument();
    expect(screen.queryByText("· No checkpoint yet")).not.toBeInTheDocument();
  });

  test("validates both manual passphrase fields before recent-auth or job creation", async () => {
    installApi();
    render(<RecoveryPanel />);
    await screen.findByText("1 · Create");

    fireEvent.click(screen.getByRole("button", { name: "Manual passphrase" }));
    fireEvent.change(screen.getByPlaceholderText("Passphrase"), { target: { value: "long-enough-passphrase" } });
    fireEvent.change(screen.getByPlaceholderText("Confirm passphrase"), { target: { value: "different-passphrase" } });
    fireEvent.click(screen.getByRole("button", { name: "Create encrypted backup" }));

    expect(await screen.findByText("Passphrases do not match.")).toBeInTheDocument();
    expect(apiRequest).not.toHaveBeenCalledWith(
      "/api/admin/backups/recent-auth/status",
      expect.anything(),
    );
    expect(apiRequest).not.toHaveBeenCalledWith(
      "/api/admin/backup-jobs",
      expect.anything(),
    );
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

    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith(
      "/api/admin/backups",
      expect.objectContaining({ signal: expect.anything() }),
    ));
    fireEvent.click(screen.getByRole("button", { name: "2 · Checkpoints" }));
    fireEvent.click(await screen.findByRole("button", { name: /Manual · Admin UI/i }));
    fireEvent.click(screen.getByRole("button", { name: "3 · Verify & protect" }));
    expect(screen.getByText("Backup warning status unavailable.")).toBeInTheDocument();
    expect(screen.queryByText(/No recent backup warnings/)).not.toBeInTheDocument();
  });

  test("recent-auth dialog traps focus, clears the password on Escape, and returns focus", async () => {
    installApi({ recentAuth: false });
    render(<RecoveryPanel />);
    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith(
      "/api/admin/backups",
      expect.objectContaining({ signal: expect.anything() }),
    ));

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

  test("recent-auth network unknown shows Retry instead of a password prompt", async () => {
    installApi({ recentAuthError: true });
    render(<RecoveryPanel />);
    await screen.findByText("1 · Create");

    fireEvent.click(screen.getByRole("button", { name: "Create encrypted backup" }));

    expect(await screen.findByText("Could not verify recent authentication.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Confirm your password" })).not.toBeInTheDocument();
  });

  test("redacts host paths for remote catalog responses and warns that auto-key files are not independently portable", async () => {
    const checkpointWithPrivatePath = { ...CHECKPOINT, path: "/srv/elvern/private/backups/checkpoint.enc" };
    apiRequest.mockImplementation(async (path) => {
      if (path === "/api/admin/backups") return { backups_dir: "backups", checkpoints: [checkpointWithPrivatePath] };
      if (path === "/api/admin/audit?limit=100") return { events: [] };
      if (path === "/api/admin/backup-jobs/active") return { job: null };
      throw new Error(`Unexpected request: GET ${path}`);
    });
    render(<RecoveryPanel />);
    await screen.findByText("1 · Create");

    fireEvent.click(screen.getByRole("button", { name: "2 · Checkpoints" }));
    fireEvent.click(await screen.findByRole("button", { name: /Manual · Admin UI/i }));
    fireEvent.click(screen.getByRole("button", { name: "3 · Verify & protect" }));

    expect(screen.getByText("Server-local backup directory")).toBeInTheDocument();
    expect(screen.getByText(`backups/${CHECKPOINT.checkpoint_id}`)).toBeInTheDocument();
    expect(screen.getByText(/not portable without this server.s backup keyring/i)).toBeInTheDocument();
    expect(screen.queryByText(checkpointWithPrivatePath.path, { exact: true })).not.toBeInTheDocument();
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
    await waitFor(() => expect(apiRequest).toHaveBeenCalledWith(
      "/api/admin/backups",
      expect.objectContaining({ signal: expect.anything() }),
    ));

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

    for (let index = 0; index < 3; index += 1) {
      window.dispatchEvent(new CustomEvent(ADMIN_BACKUP_EVENT, {
        detail: { eventType: "backup_job_updated" },
      }));
    }

    expect(await screen.findByText("Interrupted")).toBeInTheDocument();
    expect(activeRequests).toBe(2);
  });

  test("pauses active-job fallback polling while hidden and refreshes when visible again", async () => {
    vi.useFakeTimers();
    const visibility = vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    let jobStatusRequests = 0;
    apiRequest.mockImplementation(async (path) => {
      if (path === "/api/admin/backups") return { backups_dir: "backups", checkpoints: [CHECKPOINT] };
      if (path === "/api/admin/audit?limit=100") return { events: [] };
      if (path === "/api/admin/backup-jobs/active") {
        return { job: { ...INTERRUPTED_JOB, id: "job-running", state: "archiving", progress_percent: 44 } };
      }
      if (path === "/api/admin/backup-jobs/job-running") {
        jobStatusRequests += 1;
        return { ...INTERRUPTED_JOB, id: "job-running", state: "archiving", progress_percent: 45 };
      }
      throw new Error(`Unexpected request: GET ${path}`);
    });
    const view = render(<RecoveryPanel />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    visibility.mockReturnValue("hidden");
    document.dispatchEvent(new Event("visibilitychange"));
    await act(async () => vi.advanceTimersByTimeAsync(8_000));
    expect(jobStatusRequests).toBe(0);

    visibility.mockReturnValue("visible");
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await Promise.resolve();
    });
    expect(jobStatusRequests).toBe(1);

    view.unmount();
    visibility.mockRestore();
    vi.useRealTimers();
  });

  test("ignores a stale catalog response after the recovery identity changes", async () => {
    let resolveFirstCatalog;
    const firstCatalog = new Promise((resolve) => {
      resolveFirstCatalog = resolve;
    });
    const oldCheckpoint = { ...CHECKPOINT, checkpoint_id: "old-user-checkpoint", backup_trigger: "old_user_snapshot" };
    const newCheckpoint = { ...CHECKPOINT, checkpoint_id: "new-user-checkpoint", backup_trigger: "new_user_snapshot" };
    let catalogRequests = 0;
    apiRequest.mockImplementation(async (path) => {
      if (path === "/api/admin/backups") {
        catalogRequests += 1;
        if (catalogRequests === 1) return firstCatalog;
        return { backups_dir: "backups", checkpoints: [newCheckpoint] };
      }
      if (path === "/api/admin/audit?limit=100") return { events: [] };
      if (path === "/api/admin/backup-jobs/active") return { job: null };
      throw new Error(`Unexpected request: GET ${path}`);
    });
    const view = render(<RecoveryPanel identityKey="user-a:admin" />);
    await waitFor(() => expect(catalogRequests).toBe(1));

    view.rerender(<RecoveryPanel identityKey="user-b:admin" />);
    fireEvent.click(screen.getByRole("button", { name: "2 · Checkpoints" }));
    expect(await screen.findByText("new user snapshot")).toBeInTheDocument();

    await act(async () => {
      resolveFirstCatalog({ backups_dir: "/private/old-user", checkpoints: [oldCheckpoint] });
      await Promise.resolve();
    });
    expect(screen.queryByText("old user snapshot")).not.toBeInTheDocument();
    expect(screen.getByText("new user snapshot")).toBeInTheDocument();
  });

  test("uses neutral, amber, and red result tones according to preview meaning", async () => {
    apiRequest.mockImplementation(async (path, options = {}) => {
      if (path === "/api/admin/backups") return { backups_dir: "backups", checkpoints: [CHECKPOINT] };
      if (path === "/api/admin/audit?limit=100") return { events: [] };
      if (path === "/api/admin/backup-jobs/active") return { job: null };
      if (path === "/api/admin/backups/recent-auth/status") return { verified: true };
      if (path.endsWith("/preview") && options.method === "POST") {
        return {
          checkpoint_valid: false,
          schema_compatible: false,
          backup_counts: { users: 2 },
          current_counts: { users: 3 },
          settings_matches: { media_root: false },
          blocking_errors: ["Database integrity check failed"],
        };
      }
      throw new Error(`Unexpected request: ${options.method || "GET"} ${path}`);
    });
    render(<RecoveryPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "2 · Checkpoints" }));
    fireEvent.click(await screen.findByRole("button", { name: /Manual · Admin UI/i }));
    fireEvent.click(screen.getByRole("button", { name: "3 · Verify & protect" }));
    fireEvent.click(screen.getByRole("button", { name: "Preview recovery" }));

    expect((await screen.findByText("× checkpoint has blocking errors")).className).toBe("is-bad");
    expect(screen.getByText("● schema differs").className).toBe("is-warn");
    expect(screen.getByText("● users: backup 2 → live 3").className).toBe("is-warn");
    expect(screen.getByText("● media index: backup unknown → live unknown").className).toBe("is-muted");
    expect(screen.getByText("× Database integrity check failed").className).toBe("is-bad");
  });

  test("clears the JUST CREATED marker after the bounded highlight interval", async () => {
    vi.useFakeTimers();
    const completedJob = {
      id: "job-completed",
      state: "completed",
      progress_percent: 100,
      message: "Completed",
      checkpoint_id: CHECKPOINT.checkpoint_id,
    };
    apiRequest.mockImplementation(async (path) => {
      if (path === "/api/admin/backups") return { backups_dir: "backups", checkpoints: [CHECKPOINT] };
      if (path === "/api/admin/audit?limit=100") return { events: [] };
      if (path === "/api/admin/backup-jobs/active") return { job: completedJob };
      throw new Error(`Unexpected request: GET ${path}`);
    });
    const view = render(<RecoveryPanel />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(screen.getByText("JUST CREATED")).toBeInTheDocument();
    await act(async () => vi.advanceTimersByTimeAsync(2_599));
    expect(screen.getByText("JUST CREATED")).toBeInTheDocument();
    await act(async () => vi.advanceTimersByTimeAsync(1));
    expect(screen.queryByText("JUST CREATED")).not.toBeInTheDocument();

    view.unmount();
    vi.useRealTimers();
  });
});
