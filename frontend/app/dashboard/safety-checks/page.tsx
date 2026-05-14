import { redirect } from "next/navigation";

export default function SafetyChecksLegacyPage() {
  redirect("/dashboard/audit?section=safety-checks");
}
