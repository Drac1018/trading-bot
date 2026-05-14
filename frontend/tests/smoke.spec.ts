import { expect, test } from "@playwright/test";

test("audit page keeps selected tab from query string", async ({ page }) => {
  await page.goto("/dashboard/audit?tab=execution");

  await expect(page.getByRole("tab", { name: /실행/ })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("heading", { name: /감사 이벤트 탐색/ })).toBeVisible();
});

test("audit page switches tabs with accessible tab buttons", async ({ page }) => {
  await page.goto("/dashboard/audit");

  await page.getByRole("tab", { name: /리스크/ }).click();

  await expect(page.getByRole("tab", { name: /리스크/ })).toHaveAttribute("aria-selected", "true");
  await expect(page).toHaveURL(/tab=risk/);
});

test("cost breakdown page renders monthly summary and daily buckets", async ({ page }) => {
  await page.goto("/dashboard/cost-breakdown?period=month&year=2026&month=5");

  await expect(page).toHaveURL(/\/dashboard\/analytics\?section=cost&period=month&year=2026&month=5/);
  await expect(page.getByRole("heading", { name: "기간별 손익과 비용 분해" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "월간 손익과 비용 분해" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "2026-05-01" })).toBeVisible();
  await expect(page.getByText("운영 개요로 돌아가기")).toBeVisible();
});

test("cost breakdown page renders yearly summary and monthly buckets", async ({ page }) => {
  await page.goto("/dashboard/cost-breakdown?period=year&year=2026");

  await expect(page).toHaveURL(/\/dashboard\/analytics\?section=cost&period=year&year=2026/);
  await expect(page.getByRole("heading", { name: "기간별 손익과 비용 분해" })).toBeVisible();
  await expect(page.getByText("연간 손익과 비용 분해")).toBeVisible();
  await expect(page.getByRole("cell", { name: "2026-05" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "총 비용 / 총손익" })).toBeVisible();
});
