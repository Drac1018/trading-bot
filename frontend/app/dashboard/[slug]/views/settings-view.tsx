"use client";

import { SettingsControls } from "../../../../components/settings-controls";
import { normalizeSettingsView } from "../../../../lib/page-config";

import type { DashboardViewModuleProps } from "../dashboard-view-types";

function queryValue(value: string | string[] | undefined) {
  if (Array.isArray(value)) {
    return value[0] ?? null;
  }
  return value ?? null;
}

export function SettingsDashboardView({
  query,
  settingsPayload,
}: DashboardViewModuleProps) {
  if (!settingsPayload) {
    return null;
  }

  return (
    <SettingsControls
      initial={settingsPayload}
      initialView={normalizeSettingsView(queryValue(query.view))}
    />
  );
}
