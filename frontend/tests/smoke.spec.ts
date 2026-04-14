import { expect, test } from "@playwright/test";

test("audit page keeps selected tab from query string", async ({ page }) => {
  await page.goto("/dashboard/audit?tab=execution");

  await expect(page.getByRole("tab", { name: /실행/ })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("heading", { name: "운영 감사 이벤트 탐색" })).toBeVisible();
});

test("audit page switches tabs with accessible tab buttons", async ({ page }) => {
  await page.goto("/dashboard/audit");

  await page.getByRole("tab", { name: /리스크/ }).click();

  await expect(page.getByRole("tab", { name: /리스크/ })).toHaveAttribute("aria-selected", "true");
  await expect(page).toHaveURL(/tab=risk/);
});
