import { redirect } from "next/navigation";
import { getCurrentUser } from "@/lib/auth";
import Settings from "@/components/Settings";

export default async function SettingsPage() {
  const user = await getCurrentUser();
  if (!user) redirect("/login");
  return <Settings user={user} />;
}
