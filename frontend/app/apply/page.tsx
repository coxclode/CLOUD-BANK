"use client";

import { useState } from "react";

type Decision = {
  decision_id: string;
  outcome: string;
  confidence: number;
  risk_band: string;
  credit_terms?: { approved_amount: number; interest_rate_annual: number; monthly_installment: number } | null;
  rejection_reasons?: string[];
};

type DniStatus = "idle" | "validating" | "valid" | "invalid";

const OUTCOME_LABEL: Record<string, string> = {
  APPROVED: "Aprobado",
  REJECTED: "Rechazado",
  MORE_DOCS: "Documentos requeridos",
  ESCALATED: "Escalado a comité",
};

const EMPLOYMENT_TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: "EMPLOYED", label: "Empleado/a (dependiente)" },
  { value: "SELF_EMPLOYED", label: "Independiente" },
  { value: "FREELANCER", label: "Freelancer / por proyecto" },
  { value: "BUSINESS_OWNER", label: "Empresario/a" },
  { value: "PUBLIC_SECTOR", label: "Empleado/a público" },
  { value: "INFORMAL", label: "Trabajador/a informal" },
  { value: "RETIRED", label: "Jubilado/a" },
  { value: "STUDENT", label: "Estudiante" },
  { value: "HOMEMAKER", label: "Ama/o de casa" },
  { value: "UNEMPLOYED", label: "Desempleado/a" },
];

const DNI_RE = /^\d{8}$/;

export default function ApplyPage() {
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [decision, setDecision] = useState<Decision | null>(null);

  const [dni, setDni] = useState("");
  const [dniStatus, setDniStatus] = useState<DniStatus>("idle");
  const [dniMessage, setDniMessage] = useState<string | null>(null);
  const [fullName, setFullName] = useState("");

  const dniValidated = dniStatus === "valid";

  function handleDniChange(value: string) {
    setDni(value);
    setDniStatus("idle");
    setDniMessage(null);
    setFullName("");
  }

  async function handleValidateDni() {
    if (!DNI_RE.test(dni)) {
      setDniStatus("invalid");
      setDniMessage("El DNI debe tener 8 dígitos.");
      return;
    }

    setDniStatus("validating");
    setDniMessage(null);

    try {
      const response = await fetch(`/api/proxy/identity/${dni}`);
      const body = await response.json();

      if (!response.ok) {
        setDniStatus("invalid");
        setDniMessage(
          response.status === 404
            ? "No se encontró el DNI en RENIEC."
            : "No se pudo validar el DNI. Intenta nuevamente."
        );
        return;
      }

      const composedName = [body.first_name, body.first_last_name, body.second_last_name]
        .filter(Boolean)
        .join(" ");
      setFullName(composedName || body.full_name || "");
      setDniStatus("valid");
    } catch {
      setDniStatus("invalid");
      setDniMessage("No se pudo conectar con el servicio de validación.");
    }
  }

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!dniValidated) return;

    setLoading(true);
    setError(null);
    setDecision(null);

    const form = new FormData(e.currentTarget);
    const payload = {
      applicant: {
        full_name: fullName,
        national_id: dni,
        birth_date: form.get("birth_date"),
        email: form.get("email"),
        phone: form.get("phone"),
        employment_type: form.get("employment_type"),
        gross_monthly_income: Number(form.get("gross_monthly_income")),
        years_of_employment: Number(form.get("years_of_employment")),
        country_code: "PE",
        city: form.get("city"),
      },
      credit_request: {
        requested_amount: Number(form.get("requested_amount")),
        currency: "USD",
        term_months: Number(form.get("term_months")),
        purpose: form.get("purpose"),
        channel: "DIGITAL",
      },
      consent_given: true,
    };

    const response = await fetch("/api/proxy/evaluate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await response.json();
    setLoading(false);

    if (response.ok) {
      setDecision(body);
    } else {
      setError(body?.detail ? JSON.stringify(body.detail) : "No se pudo evaluar la solicitud.");
    }
  }

  return (
    <main>
      <h1 className="text-2xl font-semibold mb-6">Solicitud de crédito personal</h1>

      <form onSubmit={handleSubmit} className="space-y-4 rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
        <div className="grid grid-cols-2 gap-4">
          <label className="block text-sm">
            <span className="mb-1 block font-medium">Documento de identidad (DNI)</span>
            <div className="flex gap-2">
              <input
                className="w-full rounded-md border border-slate-300 px-3 py-2 disabled:bg-slate-100"
                value={dni}
                onChange={(e) => handleDniChange(e.target.value)}
                disabled={dniStatus === "validating" || dniValidated}
                placeholder="8 dígitos"
                maxLength={8}
                required
              />
              {dniValidated ? (
                <button
                  type="button"
                  onClick={() => handleDniChange("")}
                  className="shrink-0 rounded-md border border-slate-300 px-3 py-2 text-sm hover:bg-slate-50"
                >
                  Cambiar
                </button>
              ) : (
                <button
                  type="button"
                  onClick={handleValidateDni}
                  disabled={dniStatus === "validating"}
                  className="shrink-0 rounded-md bg-slate-900 px-3 py-2 text-sm text-white hover:bg-slate-700 disabled:opacity-50"
                >
                  {dniStatus === "validating" ? "Validando…" : "Validar"}
                </button>
              )}
            </div>
            {dniStatus === "valid" && <p className="mt-1 text-sm text-green-700">DNI validado ✓</p>}
            {dniStatus === "invalid" && dniMessage && (
              <p className="mt-1 text-sm text-red-600">{dniMessage}</p>
            )}
          </label>

          <label className="block text-sm">
            <span className="mb-1 block font-medium">Nombre completo</span>
            <input
              className="w-full rounded-md border border-slate-300 px-3 py-2 disabled:bg-slate-100"
              value={fullName}
              readOnly
              disabled={!dniValidated}
              placeholder="Se completa al validar el DNI"
            />
          </label>

          <Field label="Fecha de nacimiento" name="birth_date" type="date" required disabled={!dniValidated} />
          <Field label="Email" name="email" type="email" required disabled={!dniValidated} />
          <Field
            label="Teléfono (+código país)"
            name="phone"
            placeholder="+51987654321"
            required
            disabled={!dniValidated}
          />
          <SelectField
            label="Tipo de empleo"
            name="employment_type"
            options={EMPLOYMENT_TYPE_OPTIONS}
            disabled={!dniValidated}
          />
          <Field
            label="Ingreso mensual bruto"
            name="gross_monthly_income"
            type="number"
            required
            disabled={!dniValidated}
          />
          <Field
            label="Años de empleo"
            name="years_of_employment"
            type="number"
            step="0.5"
            required
            disabled={!dniValidated}
          />
          <label className="block text-sm">
            <span className="mb-1 block font-medium">País</span>
            <input
              className="w-full rounded-md border border-slate-300 bg-slate-100 px-3 py-2 text-slate-500"
              value="Perú (PE)"
              disabled
              readOnly
            />
          </label>
          <Field label="Ciudad" name="city" required disabled={!dniValidated} />
          <Field label="Monto solicitado" name="requested_amount" type="number" required disabled={!dniValidated} />
          <Field
            label="Plazo (meses)"
            name="term_months"
            type="number"
            min={6}
            max={84}
            required
            disabled={!dniValidated}
          />
          <SelectField
            label="Propósito"
            name="purpose"
            options={["PERSONAL", "HOME_IMPROVEMENT", "DEBT_CONSOLIDATION", "MEDICAL", "EDUCATION", "VEHICLE", "BUSINESS", "TRAVEL", "OTHER"].map(
              (v) => ({ value: v, label: v })
            )}
            disabled={!dniValidated}
          />
        </div>

        {error && <p className="text-sm text-red-600">{error}</p>}

        <button
          type="submit"
          disabled={loading || !dniValidated}
          className="w-full rounded-md bg-slate-900 px-4 py-2 text-white hover:bg-slate-700 disabled:opacity-50"
        >
          {loading ? "Evaluando…" : "Enviar solicitud"}
        </button>
      </form>

      {decision && (
        <div className="mt-6 rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
          <h2 className="text-lg font-semibold mb-2">
            Resultado: {OUTCOME_LABEL[decision.outcome] ?? decision.outcome}
          </h2>
          <p className="text-sm text-slate-600">Confianza: {(decision.confidence * 100).toFixed(0)}%</p>
          <p className="text-sm text-slate-600">Banda de riesgo: {decision.risk_band}</p>
          {decision.credit_terms && (
            <div className="mt-2 text-sm">
              <p>Monto aprobado: {decision.credit_terms.approved_amount}</p>
              <p>Tasa anual: {decision.credit_terms.interest_rate_annual}%</p>
              <p>Cuota mensual: {decision.credit_terms.monthly_installment}</p>
            </div>
          )}
          {decision.rejection_reasons && decision.rejection_reasons.length > 0 && (
            <ul className="mt-2 list-disc pl-5 text-sm text-red-600">
              {decision.rejection_reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </main>
  );
}

function Field(props: {
  label: string;
  name: string;
  type?: string;
  required?: boolean;
  placeholder?: string;
  step?: string;
  min?: number;
  max?: number;
  disabled?: boolean;
}) {
  const { label, ...rest } = props;
  return (
    <label className="block text-sm">
      <span className="mb-1 block font-medium">{label}</span>
      <input className="w-full rounded-md border border-slate-300 px-3 py-2 disabled:bg-slate-100" {...rest} />
    </label>
  );
}

function SelectField(props: {
  label: string;
  name: string;
  options: { value: string; label: string }[];
  disabled?: boolean;
}) {
  return (
    <label className="block text-sm">
      <span className="mb-1 block font-medium">{props.label}</span>
      <select
        name={props.name}
        className="w-full rounded-md border border-slate-300 px-3 py-2 disabled:bg-slate-100"
        disabled={props.disabled}
        required
      >
        {props.options.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
    </label>
  );
}
