import { redirect } from "next/navigation";
import { getCurrentUser } from "@/lib/auth";
import Chat from "@/components/Chat";

export default async function Home() {
  const user = await getCurrentUser();
  if (!user) redirect("/login");
  return <Chat user={user} />;
}
