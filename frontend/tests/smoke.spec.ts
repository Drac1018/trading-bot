import { expect, test } from "@playwright/test";

test("overview loads with dashboard title", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Operator Console")).toBeVisible();
  await expect(page.getByRole("link", { name: "Overview" })).toBeVisible();
});

