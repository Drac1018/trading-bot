import { redirect } from "next/navigation";

export const dynamic = "force-dynamic";

export default function SafetyChecksLegacyPage() {
  redirect("/dashboard/audit?section=safety-checks");
}
