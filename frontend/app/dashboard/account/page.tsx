import { redirect } from "next/navigation";

export default function AccountLegacyPage() {
  redirect("/dashboard/trading?section=account");
}
