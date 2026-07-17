import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { getApplicationStatus, BackendError } from "@/lib/backend-client";

export default async function StatusPage({ params }: { params: { id: string } }) {
  const token = cookies().get("cloudbank_session")?.value;
  if (!token) {
    redirect("/login");
  }

  let status: Record<string, unknown> | null = null;
  let error: string | null = null;

  try {
    status = await getApplicationStatus(token as string, params.id);
  } catch (e) {
    error = e instanceof BackendError ? e.message : "Error al conectar con el backend";
  }

  return (
    <main>
      <h1 className="text-2xl font-semibold mb-6">Estado de la solicitud</h1>
      <div className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
        {error && <p className="text-sm text-red-600">{error}</p>}
        {status && <pre className="whitespace-pre-wrap text-sm">{JSON.stringify(status, null, 2)}</pre>}
      </div>
    </main>
  );
}
