import { redirect } from "next/navigation";

export const dynamic = "force-dynamic";

export default function AccountLegacyPage() {
  redirect("/dashboard/trading?section=account");
}
