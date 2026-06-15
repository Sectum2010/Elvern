import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { test } from "vitest";
import { fileURLToPath } from "node:url";


const SRC_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const LOGIN_PAGE = "pages/LoginPage.jsx";
const SELF = "lib/passwordAutofillGuards.test.js";


function listSourceFiles(directory) {
  const entries = fs.readdirSync(directory, { withFileTypes: true });
  return entries.flatMap((entry) => {
    const fullPath = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      return listSourceFiles(fullPath);
    }
    if (!/\.(js|jsx|mjs)$/.test(entry.name)) {
      return [];
    }
    return [fullPath];
  });
}


function relativeSourcePath(filePath) {
  return path.relative(SRC_DIR, filePath).split(path.sep).join("/");
}


function readFrontendFiles() {
  return listSourceFiles(SRC_DIR)
    .map((filePath) => ({
      filePath,
      relativePath: relativeSourcePath(filePath),
      source: fs.readFileSync(filePath, "utf8"),
    }))
    .filter((entry) => entry.relativePath !== SELF);
}


test("login page is the only current-password and generic password-name surface", () => {
  const forbiddenCurrentSecretAutocomplete = /\bauto(?:C|c)omplete\s*=\s*["']current-password["']/;
  const forbiddenPasswordNameOrId = /\b(?:name|id)\s*=\s*["'][^"']*password[^"']*["']/i;

  for (const entry of readFrontendFiles()) {
    if (entry.relativePath === LOGIN_PAGE) {
      continue;
    }
    assert.doesNotMatch(
      entry.source,
      forbiddenCurrentSecretAutocomplete,
      `${entry.relativePath} must not use current-password autocomplete`,
    );
    assert.doesNotMatch(
      entry.source,
      forbiddenPasswordNameOrId,
      `${entry.relativePath} must not use password-looking name/id attributes`,
    );
  }

  const loginSource = fs.readFileSync(path.join(SRC_DIR, LOGIN_PAGE), "utf8");
  assert.match(loginSource, /\bautoComplete\s*=\s*["']current-password["']/);
  assert.match(loginSource, /\bname\s*=\s*["']password["']/);
});


test("admin and signup password surfaces use the hardened non-login input", () => {
  const adminSource = fs.readFileSync(path.join(SRC_DIR, "pages/AdminPage.jsx"), "utf8");
  assert.match(adminSource, /NonLoginSecretInput/);
  assert.doesNotMatch(adminSource, /import\s+\{\s*PasswordInput\s*\}/);
  assert.doesNotMatch(adminSource, /\bautoComplete\s*=\s*["']current-password["']/);

  const newUserSource = fs.readFileSync(path.join(SRC_DIR, "pages/NewUserPage.jsx"), "utf8");
  assert.match(newUserSource, /NonLoginSecretInput/);
  assert.doesNotMatch(newUserSource, /\bautoComplete\s*=\s*["']current-password["']/);
  assert.doesNotMatch(newUserSource, /\b(?:name|id)\s*=\s*["'][^"']*password[^"']*["']/i);
});


test("non-login secret input never puts purpose or password-like words in DOM name or id", () => {
  const source = fs.readFileSync(path.join(SRC_DIR, "components/NonLoginSecretInput.jsx"), "utf8");
  assert.match(source, /fieldName\s*=\s*`elvern-secret-\$\{tokenRef\.current\}`/);
  assert.doesNotMatch(source, /fieldName\s*=\s*`[^`]*purpose/i);
  assert.doesNotMatch(source, /fieldName\s*=\s*`[^`]*password/i);
  assert.doesNotMatch(source, /\b(?:name|id)\s*=\s*\{[^}]*purpose[^}]*\}/i);
});


test("destructive or admin password state is not persisted in browser storage", () => {
  const sensitiveStatePattern = /currentAdminPassword|deleteUserState|selfDeleteState|passwordEditor|createUserForm|password/i;
  const storagePattern = /(?:localStorage|sessionStorage)\s*\./;

  for (const entry of readFrontendFiles()) {
    const lines = entry.source.split(/\r?\n/);
    lines.forEach((line, index) => {
      assert.ok(
        !(storagePattern.test(line) && sensitiveStatePattern.test(line)),
        `${entry.relativePath}:${index + 1} must not persist password/destructive secret state`,
      );
    });
  }
});


test("invite code plaintext is not persisted in browser storage", () => {
  const inviteStoragePattern = /(?:localStorage|sessionStorage)\s*\.[^\n]*(?:invite|Invite|INVITE)/;
  for (const entry of readFrontendFiles()) {
    assert.doesNotMatch(entry.source, inviteStoragePattern, `${entry.relativePath} must not persist invite codes`);
  }
});


test("signup invite code is not URL-prefilled", () => {
  const newUserSource = fs.readFileSync(path.join(SRC_DIR, "pages/NewUserPage.jsx"), "utf8");
  assert.doesNotMatch(newUserSource, /URLSearchParams/);
  assert.doesNotMatch(newUserSource, /location\.search/);
  assert.doesNotMatch(newUserSource, /useSearchParams/);
});
