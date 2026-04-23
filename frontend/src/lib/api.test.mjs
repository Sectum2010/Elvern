import test from "node:test";
import assert from "node:assert/strict";

import { extractApiErrorMessage } from "./api.js";

test("extractApiErrorMessage returns string detail directly", () => {
  assert.equal(
    extractApiErrorMessage({ detail: "Google OAuth Client Secret must not contain spaces." }),
    "Google OAuth Client Secret must not contain spaces.",
  );
});

test("extractApiErrorMessage uses object detail message", () => {
  assert.equal(
    extractApiErrorMessage({ detail: { message: "Reconnect Google Drive to continue this action." } }),
    "Reconnect Google Drive to continue this action.",
  );
});

test("extractApiErrorMessage joins FastAPI validation detail entries", () => {
  assert.equal(
    extractApiErrorMessage({
      detail: [
        { loc: ["body", "resource_id"], msg: "String should have at least 2 characters" },
        { loc: ["body", "resource_type"], msg: "Input should be 'folder' or 'shared_drive'" },
      ],
    }),
    "body.resource_id: String should have at least 2 characters; body.resource_type: Input should be 'folder' or 'shared_drive'",
  );
});

test("extractApiErrorMessage falls back to plain text error bodies", () => {
  assert.equal(
    extractApiErrorMessage("Cloud libraries refresh failed upstream."),
    "Cloud libraries refresh failed upstream.",
  );
});

test("extractApiErrorMessage uses object-level message when detail is absent", () => {
  assert.equal(
    extractApiErrorMessage({ message: "Cloud libraries could not refresh." }),
    "Cloud libraries could not refresh.",
  );
});
