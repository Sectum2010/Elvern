const SAFE_FILENAME_PATTERN = /^[A-Za-z0-9._-]+$/;
const SAFE_PACKAGE_ROOT_PATTERN = /^[A-Za-z0-9 ._-]+$/;
const SAFE_RELATIVE_PATH_PATTERN = /^(?!\/)(?!.*(?:^|\/)\.{1,2}(?:\/|$))[A-Za-z0-9._/-]+$/;
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
  const treeManifestPath = requireSafeReleaseField(
    release?.installer_tree_manifest_path,
    SAFE_RELATIVE_PATH_PATTERN,
    "installer tree manifest path",
  );
  const treeManifestSha = requireSafeReleaseField(
    release?.installer_tree_manifest_sha256,
    SHA256_PATTERN,
    "installer tree manifest SHA-256",
  ).toLowerCase();

  return `set -euo pipefail
ZIP="$HOME/Downloads/${filename}"
EXTRACTED="$HOME/Downloads/${packageRoot}"
EXPECTED_ZIP_SHA="${archiveSha}"
EXPECTED_MANIFEST_SHA="${manifestSha}"
TREE_MANIFEST_RELATIVE="${treeManifestPath}"
EXPECTED_TREE_MANIFEST_SHA="${treeManifestSha}"
TEMP_DIR=""
PACKAGE_DIR=""
EXPECTED_FILES=""
ACTUAL_FILES=""
cleanup() {
  if [ -n "$TEMP_DIR" ] && [ -d "$TEMP_DIR" ]; then /bin/rm -rf "$TEMP_DIR"; fi
  if [ -n "$EXPECTED_FILES" ]; then /bin/rm -f "$EXPECTED_FILES"; fi
  if [ -n "$ACTUAL_FILES" ]; then /bin/rm -f "$ACTUAL_FILES"; fi
}
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
MANIFEST="$PACKAGE_DIR/.elvern/manifest.json"
TREE_MANIFEST="$PACKAGE_DIR/$TREE_MANIFEST_RELATIVE"
[ -f "$MANIFEST" ] && [ -f "$TREE_MANIFEST" ] || { echo "Elvern verification metadata is missing." >&2; exit 1; }
[ "$(/usr/bin/shasum -a 256 "$MANIFEST" | /usr/bin/awk '{print $1}')" = "$EXPECTED_MANIFEST_SHA" ] || { echo "Elvern installer manifest SHA-256 mismatch." >&2; exit 1; }
[ "$(/usr/bin/shasum -a 256 "$TREE_MANIFEST" | /usr/bin/awk '{print $1}')" = "$EXPECTED_TREE_MANIFEST_SHA" ] || { echo "Elvern tree manifest SHA-256 mismatch." >&2; exit 1; }
EXPECTED_FILES="$(mktemp)"
ACTUAL_FILES="$(mktemp)"
while IFS="$(printf '\\t')" read -r RELATIVE SIZE DIGEST FILE_CLASS EXTRA; do
  [ "$RELATIVE" = "path" ] && continue
  [ -z "$EXTRA" ] || { echo "Invalid Elvern tree manifest row." >&2; exit 1; }
  case "/$RELATIVE/" in */../*|*/./*) echo "Unsafe Elvern package path." >&2; exit 1 ;; esac
  case "$RELATIVE" in /*|*\\\\*) echo "Unsafe Elvern package path." >&2; exit 1 ;; esac
  FILE="$PACKAGE_DIR/$RELATIVE"
  [ -f "$FILE" ] && [ ! -L "$FILE" ] || { echo "Elvern package file is missing or unsafe." >&2; exit 1; }
  [ "$(/usr/bin/wc -c < "$FILE" | /usr/bin/tr -d '[:space:]')" = "$SIZE" ] || { echo "Elvern package file size mismatch." >&2; exit 1; }
  [ "$(/usr/bin/shasum -a 256 "$FILE" | /usr/bin/awk '{print $1}')" = "$DIGEST" ] || { echo "Elvern package file SHA-256 mismatch." >&2; exit 1; }
  printf '%s\\n' "$RELATIVE" >> "$EXPECTED_FILES"
done < "$TREE_MANIFEST"
/usr/bin/find "$PACKAGE_DIR" -type f -print | while IFS= read -r FILE; do
  RELATIVE="\${FILE#"$PACKAGE_DIR/"}"
  [ "$RELATIVE" = "$TREE_MANIFEST_RELATIVE" ] && continue
  [ "\${RELATIVE##*/}" = ".DS_Store" ] && continue
  printf '%s\\n' "$RELATIVE"
done > "$ACTUAL_FILES"
LC_ALL=C /usr/bin/sort -o "$EXPECTED_FILES" "$EXPECTED_FILES"
LC_ALL=C /usr/bin/sort -o "$ACTUAL_FILES" "$ACTUAL_FILES"
/usr/bin/cmp -s "$EXPECTED_FILES" "$ACTUAL_FILES" || { echo "Elvern package contains a missing or unexpected file." >&2; exit 1; }
/bin/rm -f "$EXPECTED_FILES" "$ACTUAL_FILES"
while IFS="$(printf '\\t')" read -r RELATIVE _REST; do
  [ "$RELATIVE" = "path" ] && continue
  FILE="$PACKAGE_DIR/$RELATIVE"
  if /usr/bin/xattr -p com.apple.quarantine "$FILE" >/dev/null 2>&1; then
    /usr/bin/xattr -d com.apple.quarantine "$FILE" || { echo "Could not remove quarantine from a verified Elvern file." >&2; exit 1; }
  fi
done < "$TREE_MANIFEST"
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
