const SAFE_FILENAME_PATTERN = /^[A-Za-z0-9._-]+$/;
const SAFE_PACKAGE_ROOT_PATTERN = /^[A-Za-z0-9 ._-]+$/;
const SHA256_PATTERN = /^[a-f0-9]{64}$/i;


function requireSafeReleaseField(value, pattern, fieldName) {
  const normalized = String(value || "").trim();
  if (!normalized || !pattern.test(normalized)) {
    throw new Error(`Unsafe or missing desktop helper ${fieldName}`);
  }
  return normalized;
}


export function isPackageLevelDesktopHelperRelease(release) {
  return Boolean(
    release
    && release.deployment_mode === "self_contained"
    && release.external_runtime_required === false
    && Array.isArray(release.supported_runtime_ids)
    && release.supported_runtime_ids.length > 0,
  );
}


export function buildMacTerminalInstallCommand(release) {
  const filename = requireSafeReleaseField(release?.filename, SAFE_FILENAME_PATTERN, "filename");
  const packageRoot = requireSafeReleaseField(
    release?.package_root,
    SAFE_PACKAGE_ROOT_PATTERN,
    "package root",
  );
  const installer = requireSafeReleaseField(
    release?.installer_entrypoint,
    SAFE_FILENAME_PATTERN,
    "installer entrypoint",
  );
  const archiveSha = requireSafeReleaseField(release?.sha256, SHA256_PATTERN, "archive SHA-256").toLowerCase();
  const manifestSha = requireSafeReleaseField(
    release?.installer_manifest_sha256,
    SHA256_PATTERN,
    "installer manifest SHA-256",
  ).toLowerCase();

  return `set -euo pipefail
ZIP="$HOME/Downloads/${filename}"
EXTRACTED="$HOME/Downloads/${packageRoot}"
EXPECTED_ZIP_SHA="${archiveSha}"
EXPECTED_MANIFEST_SHA="${manifestSha}"
TEMP_DIR=""
PACKAGE_DIR=""
cleanup() { if [ -n "$TEMP_DIR" ] && [ -d "$TEMP_DIR" ]; then /bin/rm -rf "$TEMP_DIR"; fi; }
trap cleanup EXIT
if [ -f "$ZIP" ]; then
  ACTUAL_ZIP_SHA="$(/usr/bin/shasum -a 256 "$ZIP" | /usr/bin/awk '{print $1}')"
  [ "$ACTUAL_ZIP_SHA" = "$EXPECTED_ZIP_SHA" ] || { echo "Elvern package SHA-256 mismatch." >&2; exit 1; }
  TEMP_DIR="$(mktemp -d)"
  /usr/bin/ditto -x -k "$ZIP" "$TEMP_DIR"
  PACKAGE_DIR="$TEMP_DIR/${packageRoot}"
elif [ -d "$EXTRACTED" ]; then
  MANIFEST="$EXTRACTED/.elvern/manifest.json"
  [ -f "$MANIFEST" ] || { echo "Elvern installer manifest is missing." >&2; exit 1; }
  ACTUAL_MANIFEST_SHA="$(/usr/bin/shasum -a 256 "$MANIFEST" | /usr/bin/awk '{print $1}')"
  [ "$ACTUAL_MANIFEST_SHA" = "$EXPECTED_MANIFEST_SHA" ] || { echo "Elvern installer manifest SHA-256 mismatch." >&2; exit 1; }
  PACKAGE_DIR="$EXTRACTED"
else
  echo "Expected $ZIP or $EXTRACTED in Downloads." >&2
  exit 1
fi
[ -d "$PACKAGE_DIR" ] || { echo "Elvern installer directory is missing." >&2; exit 1; }
/usr/bin/xattr -dr com.apple.quarantine "$PACKAGE_DIR"
/bin/bash "$PACKAGE_DIR/${installer}"`;
}


export async function copyTextToClipboard(text) {
  if (navigator?.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  const copied = document.execCommand("copy");
  textarea.remove();
  if (!copied) {
    throw new Error("Clipboard access is unavailable");
  }
}
