import { redirect } from "next/navigation";
import { cookies } from "next/headers";

export default function HomePage() {
  const hasSession = Boolean(cookies().get("cloudbank_session"));
  redirect(hasSession ? "/apply" : "/login");
}
