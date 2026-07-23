import { describe, expect, test } from "vitest";

import { buildMacTerminalInstallCommand } from "./desktopHelperInstall.js";


describe("macOS Terminal helper fallback", () => {
  test("pins the exact package and hashes without broad security bypasses", () => {
    const command = buildMacTerminalInstallCommand({
      filename: "elvern-vlc-opener-0.9.0-macos-dual-arch.zip",
      package_root: "Elvern VLC Opener Installer",
      installer_entrypoint: "Install-ElvernVlcOpener.command",
      sha256: "a".repeat(64),
      installer_manifest_sha256: "b".repeat(64),
      installer_tree_manifest_path: ".elvern/tree-manifest.tsv",
      installer_tree_manifest_sha256: "c".repeat(64),
    });

    expect(command).toContain("elvern-vlc-opener-0.9.0-macos-dual-arch.zip");
    expect(command).toContain("Elvern VLC Opener Installer");
    expect(command).toContain("a".repeat(64));
    expect(command).toContain("b".repeat(64));
    expect(command).toContain("c".repeat(64));
    expect(command).toContain("/usr/bin/shasum -a 256");
    expect(command).toContain("done < \"$TREE_MANIFEST\"");
    expect(command).toContain("/usr/bin/cmp -s \"$EXPECTED_FILES\" \"$ACTUAL_FILES\"");
    expect(command).toContain("[ ! -L \"$FILE\" ]");
    expect(command).toContain("mktemp -d");
    expect(command).toContain('if [ -n "$EXPECTED_FILES" ]; then /bin/rm -f "$EXPECTED_FILES"; fi');
    expect(command).toContain('if [ -n "$ACTUAL_FILES" ]; then /bin/rm -f "$ACTUAL_FILES"; fi');
    expect(command).toContain("/bin/bash");
    expect(command).not.toMatch(/\bsudo\b/);
    expect(command).not.toContain("spctl");
    expect(command).not.toMatch(/curl\s[^\n|]*\|\s*(ba)?sh/);
    expect(command).not.toContain("xattr -dr");
    expect(command).not.toContain("xattr -dr com.apple.quarantine \"$HOME/Downloads\"");
    expect(command).not.toContain("xattr -dr com.apple.quarantine \"$HOME/Applications\"");
    expect(command).not.toContain("*.zip");
  });

  test.each([
    ["package root", { package_root: "../Elvern" }],
    ["installer", { installer_entrypoint: "../Install.command" }],
    ["tree manifest", { installer_tree_manifest_path: "../tree-manifest.tsv" }],
    ["tree manifest dot segment", { installer_tree_manifest_path: ".elvern/./tree-manifest.tsv" }],
  ])("rejects an unsafe %s before generating a command", (_field, overrides) => {
    expect(() => buildMacTerminalInstallCommand({
      filename: "elvern-vlc-opener-0.9.0-macos-dual-arch.zip",
      package_root: "Elvern VLC Opener Installer",
      installer_entrypoint: "Install-ElvernVlcOpener.command",
      sha256: "a".repeat(64),
      installer_manifest_sha256: "b".repeat(64),
      installer_tree_manifest_path: ".elvern/tree-manifest.tsv",
      installer_tree_manifest_sha256: "c".repeat(64),
      ...overrides,
    })).toThrow(/Unsafe or missing desktop helper/);
  });
});
