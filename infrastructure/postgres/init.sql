-- CLOUD BANK — Esquema inicial de PostgreSQL
-- Se ejecuta automáticamente en el primer arranque del contenedor (imagen
-- postgres:16-alpine monta este archivo en /docker-entrypoint-initdb.d/).
-- Ver backend/src/infrastructure/persistence/postgres_repositories.py para
-- el mapeo exacto de columnas.

CREATE TABLE IF NOT EXISTS credit_applications (
    application_id    UUID PRIMARY KEY,
    applicant_id      UUID NOT NULL,
    national_id       TEXT NOT NULL,
    status            TEXT NOT NULL,
    requested_amount  NUMERIC NOT NULL,
    currency          TEXT NOT NULL,
    term_months       INT NOT NULL,
    purpose           TEXT NOT NULL,
    channel           TEXT NOT NULL,
    consent_given     BOOLEAN NOT NULL,
    correlation_id    TEXT,
    rejection_reasons JSONB DEFAULT '[]',
    reviewer_notes    TEXT DEFAULT '',
    risk_score_value  NUMERIC,
    risk_score_pd     NUMERIC,
    applicant_data    JSONB NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL,
    updated_at        TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS credit_decisions (
    decision_id            UUID PRIMARY KEY,
    application_id         UUID NOT NULL REFERENCES credit_applications(application_id),
    outcome                TEXT NOT NULL,
    confidence             NUMERIC NOT NULL,
    decided_at             TIMESTAMPTZ NOT NULL,
    decided_by             TEXT NOT NULL,
    risk_score_value       NUMERIC NOT NULL,
    default_probability    NUMERIC NOT NULL,
    credit_terms           JSONB,
    rejection_reasons      JSONB DEFAULT '[]',
    required_documents     JSONB DEFAULT '[]',
    escalation_details     JSONB,
    justification          JSONB,
    human_review_required  BOOLEAN DEFAULT FALSE,
    previous_decision_id   UUID
);

CREATE INDEX IF NOT EXISTS idx_applications_national_id ON credit_applications(national_id);
CREATE INDEX IF NOT EXISTS idx_applications_status ON credit_applications(status);
CREATE INDEX IF NOT EXISTS idx_decisions_application_id ON credit_decisions(application_id);
