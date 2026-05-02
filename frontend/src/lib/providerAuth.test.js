import test from "node:test";
import assert from "node:assert/strict";

import {
  buildProviderAuthReturnPath,
  getProviderAuthRequirement,
  getProviderAuthRequirementFromStatus,
  shouldShowProviderAuthActionModal,
  shouldShowProviderAuthBootstrapModal,
} from "./providerAuth.js";


test("provider auth helper normalizes reconnect-required detail", () => {
  const requirement = getProviderAuthRequirement({
    detail: {
      code: "provider_auth_required",
      provider: "google_drive",
      provider_reason: "token_expired_or_revoked",
      title: "Google Drive connection expired",
      message: "Reconnect Google Drive to continue this action.",
      allow_reconnect: false,
      requires_admin: true,
    },
  });

  assert.deepEqual(requirement, {
    code: "provider_auth_required",
    provider: "google_drive",
    providerReason: "token_expired_or_revoked",
    title: "Google Drive connection expired",
    message: "Reconnect Google Drive to continue this action.",
    allowReconnect: false,
    requiresAdmin: true,
  });
});


test("provider auth helper ignores quota and capacity errors", () => {
  assert.equal(
    getProviderAuthRequirement({
      detail: {
        code: "provider_quota_exceeded",
        provider: "google_drive",
      },
    }),
    null,
  );
  assert.equal(
    getProviderAuthRequirement({
      detail: {
        code: "server_max_capacity",
      },
    }),
    null,
  );
});


test("provider auth status payload normalizes bootstrap requirement", () => {
  const requirement = getProviderAuthRequirementFromStatus({
    provider_auth_required: true,
    reconnect_required: true,
    requirement: {
      code: "provider_auth_required",
      provider: "google_drive",
      provider_reason: "token_expired_or_revoked",
      title: "Google Drive connection expired",
      message: "Reconnect Google Drive to continue cloud playback.",
      allow_reconnect: true,
      requires_admin: false,
    },
  });

  assert.equal(requirement.title, "Google Drive connection expired");
  assert.equal(requirement.message, "Reconnect Google Drive to continue cloud playback.");
  assert.equal(requirement.allowReconnect, true);
  assert.equal(shouldShowProviderAuthBootstrapModal({ requirement, dismissed: false }), true);
});


test("bootstrap provider auth modal stays dismissed after Later in the same app session", () => {
  const requirement = getProviderAuthRequirementFromStatus({
    provider_auth_required: true,
    provider: "google_drive",
    provider_reason: "token_expired_or_revoked",
    title: "Google Drive connection expired",
    message: "Reconnect Google Drive to continue cloud playback.",
  });

  assert.equal(shouldShowProviderAuthBootstrapModal({ requirement, dismissed: true }), false);
});


test("cloud action reprompts after Later while local action does not", () => {
  const requirement = getProviderAuthRequirementFromStatus({
    provider_auth_required: true,
    provider: "google_drive",
    provider_reason: "token_expired_or_revoked",
  });

  assert.equal(
    shouldShowProviderAuthActionModal({ itemSourceKind: "cloud", requirement }),
    true,
  );
  assert.equal(
    shouldShowProviderAuthActionModal({ itemSourceKind: "local", requirement }),
    false,
  );
});


test("admin-required provider auth keeps reconnect disabled", () => {
  const requirement = getProviderAuthRequirementFromStatus({
    provider_auth_required: true,
    reconnect_required: true,
    requirement: {
      code: "provider_auth_required",
      provider: "google_drive",
      provider_reason: "token_expired_or_revoked",
      title: "Google Drive connection needs administrator attention",
      message: "Ask an administrator to reconnect Google Drive to continue cloud playback.",
      allow_reconnect: false,
      requires_admin: true,
    },
  });

  assert.equal(requirement.requiresAdmin, true);
  assert.equal(requirement.allowReconnect, false);
  assert.equal(requirement.title, "Google Drive connection needs administrator attention");
});


test("provider reconnect return path preserves page while dropping callback params", () => {
  const returnPath = buildProviderAuthReturnPath(
    "https://example.test/library/70?googleDriveStatus=error&googleDriveMessage=nope&view=cloud#player",
  );

  assert.equal(returnPath, "/library/70?view=cloud#player");
});
