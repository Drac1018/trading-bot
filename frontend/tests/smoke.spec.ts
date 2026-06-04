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

test("settings page exposes safe controls and explains execution profile auto apply modes", async ({ page }) => {
  await page.goto("/dashboard/settings");

  await expect(page.getByRole("heading", { name: "심볼, AI, 거래소 운영 제어" })).toBeVisible();
  await expect(page.getByRole("button", { name: "즉시 중지" })).toBeVisible();
  await expect(page.getByRole("button", { name: "승인 열기" })).toBeVisible();
  await expect(page.getByRole("button", { name: "무제한 승인" })).toBeVisible();
  await expect(page.getByRole("button", { name: "승인 닫기" })).toBeVisible();
  await expect(page.getByRole("button", { name: "거래소 동기화" })).toBeVisible();

  await page.getByText("자동 적용 모드 설명:").click();
  await expect(page.getByText("신규 진입 차단 기준")).toBeVisible();
  await expect(page.getByText("shadow 결과만으로 신규 진입을 차단하지 않습니다.")).toBeVisible();

  await page.getByRole("link", { name: /연동/ }).click();
  await expect(page).toHaveURL(/\/dashboard\/settings\?view=integration/);
  await page.getByRole("button", { name: "gpt-5-mini" }).click();
  await expect(page.getByLabel("모델")).toHaveValue("gpt-5-mini");
  await expect(page.getByRole("button", { name: "연동 설정 저장" })).toBeVisible();
});
