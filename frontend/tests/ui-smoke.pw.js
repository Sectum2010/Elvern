import { expect, test } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import path from "node:path";

function isExpectedBackendFreeConsoleError(message) {
  return [
    "Failed to load resource",
    "Failed to load session",
    "ERR_CONNECTION_REFUSED",
    "Request failed",
    "/api/auth/me",
  ].some((pattern) => message.includes(pattern));
}

test("root route loads at desktop and mobile sizes", async ({ page }, testInfo) => {
  const pageErrors = [];
  const unexpectedConsoleErrors = [];

  page.on("pageerror", (error) => {
    pageErrors.push(error.message);
  });

  page.on("console", (message) => {
    if (message.type() !== "error") {
      return;
    }
    const text = message.text();
    if (!isExpectedBackendFreeConsoleError(text)) {
      unexpectedConsoleErrors.push(text);
    }
  });

  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.locator("body").waitFor({ state: "visible" });
  await page.waitForLoadState("networkidle", { timeout: 5000 }).catch(() => {});

  await expect(page.locator("body")).toBeVisible();

  const overflow = await page.evaluate(() => {
    const root = document.documentElement;
    const body = document.body;
    const scrollWidth = Math.max(root.scrollWidth, body?.scrollWidth || 0);
    return {
      clientWidth: root.clientWidth,
      scrollWidth,
    };
  });

  expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.clientWidth + 1);

  const screenshotDir = path.join(process.cwd(), "ui-screenshots");
  await mkdir(screenshotDir, { recursive: true });
  await page.screenshot({
    fullPage: true,
    path: path.join(screenshotDir, `${testInfo.project.name}-root.png`),
  });

  expect(pageErrors).toEqual([]);
  expect(unexpectedConsoleErrors).toEqual([]);
});
