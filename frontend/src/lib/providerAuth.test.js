import test from "node:test";
import assert from "node:assert/strict";

import { getProviderAuthRequirement } from "./providerAuth.js";


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
