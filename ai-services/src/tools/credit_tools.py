"""
CLOUD BANK — Herramientas del Agente de Historial Crediticio
Wrappers sobre: bureaus de crédito, AML, verificación de ingresos, análisis de gastos.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Optional

import httpx
import structlog

from src.core.state import (
    CreditBureauData,
    ExpenseAnalysis,
    IncomeAnalysis,
)
from src.observability.tracer import trace_external_call

logger = structlog.get_logger(__name__)
_HTTP_TIMEOUT = httpx.Timeout(12.0, connect=3.0)


async def query_credit_bureau(
    national_id: str,
    tax_id: Optional[str] = None,
) -> CreditBureauData:
    """
    Consulta los bureaus de crédito primario y secundario.
    En producción: Equifax / TransUnion / Experian (multi-bureau).
    """
    id_hash = hashlib.sha256(national_id.encode()).hexdigest()

    async with trace_external_call("credit_bureau", "query"):
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                bureau_url = os.environ.get("CREDIT_BUREAU_URL", "http://localhost:9004")
                response = await client.post(
                    f"{bureau_url}/api/v2/credit/report",
                    json={"national_id_hash": id_hash, "tax_id": tax_id},
                    headers={"Authorization": f"Bearer {os.environ.get('CREDIT_BUREAU_API_KEY', '')}"},
                )
                response.raise_for_status()
                data = response.json()
                return CreditBureauData(
                    bureau_name=data.get("bureau", "PRIMARY"),
                    credit_score=int(data.get("credit_score", 0)),
                    score_model=data.get("score_model", "FICO_9"),
                    total_accounts=int(data.get("total_accounts", 0)),
                    open_accounts=int(data.get("open_accounts", 0)),
                    delinquent_accounts=int(data.get("delinquent_accounts", 0)),
                    total_debt=float(data.get("total_debt", 0.0)),
                    credit_utilization=float(data.get("credit_utilization", 0.0)),
                    oldest_account_months=int(data.get("oldest_account_months", 0)),
                    payment_history_score=float(data.get("payment_history_score", 0.0)),
                    negative_marks=data.get("negative_marks", []),
                    bankruptcy_history=bool(data.get("bankruptcy_history", False)),
                )
        except Exception as e:
            logger.warning("query_credit_bureau.failed", error=str(e))
            raise


async def run_aml_check(national_id: str, full_name: str) -> dict[str, Any]:
    """
    Verifica contra listas de AML: OFAC, ONU, PEPs, sanciones locales.
    Devuelve dict con {clear: bool, flags: list, lists_checked: list}.
    """
    id_hash  = hashlib.sha256(national_id.encode()).hexdigest()
    name_hash = hashlib.sha256(full_name.lower().encode()).hexdigest()

    async with trace_external_call("aml_service", "screen"):
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                aml_url = os.environ.get("AML_SERVICE_URL", "http://localhost:9005")
                response = await client.post(
                    f"{aml_url}/api/v1/screen",
                    json={"id_hash": id_hash, "name_hash": name_hash},
                    headers={"Authorization": f"Bearer {os.environ.get('AML_API_KEY', '')}"},
                )
                response.raise_for_status()
                data = response.json()
                return {
                    "clear": bool(data.get("clear", True)),
                    "flags": data.get("flags", []),
                    "lists_checked": data.get("lists_checked", ["OFAC", "UN", "EU"]),
                    "match_score": float(data.get("match_score", 0.0)),
                }
        except Exception as e:
            logger.warning("run_aml_check.failed", error=str(e))
            return {"clear": True, "flags": ["AML_SERVICE_UNAVAILABLE"], "lists_checked": []}


async def verify_income_sources(
    declared_income: float,
    employment_type: str,
    employer_name: Optional[str],
    employment_months: int,
) -> IncomeAnalysis:
    """
    Verifica ingresos contra registros tributarios y planillas de pago.
    Calcula el ingreso verificado y la estabilidad laboral.
    """
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            bureau_url = os.environ.get("CREDIT_BUREAU_URL", "http://localhost:9004")
            response = await client.post(
                f"{bureau_url}/api/v2/income/verify",
                json={
                    "declared_income": declared_income,
                    "employment_type": employment_type,
                    "employer": employer_name,
                    "employment_months": employment_months,
                },
                headers={"Authorization": f"Bearer {os.environ.get('CREDIT_BUREAU_API_KEY', '')}"},
            )
            response.raise_for_status()
            data = response.json()

            verified = float(data.get("verified_income", declared_income * 0.9))
            discrepancy = abs(declared_income - verified) / max(declared_income, 1)

            return IncomeAnalysis(
                declared_monthly_income=declared_income,
                verified_monthly_income=verified,
                income_verification_method=data.get("method", "TAX_RECORDS"),
                income_stability_score=float(data.get("stability_score", 0.7)),
                income_source=data.get("income_source", employment_type),
                employment_type=employment_type,
                employment_duration_months=employment_months,
                income_consistency=discrepancy < 0.20,
                income_discrepancy_flag=discrepancy >= 0.20,
            )
    except Exception as e:
        logger.warning("verify_income_sources.failed", error=str(e))
        stability = min(1.0, employment_months / 24)
        return IncomeAnalysis(
            declared_monthly_income=declared_income,
            verified_monthly_income=declared_income * 0.85,
            income_verification_method="DECLARED_ONLY",
            income_stability_score=stability,
            income_source=employment_type,
            employment_type=employment_type,
            employment_duration_months=employment_months,
            income_consistency=True,
            income_discrepancy_flag=False,
        )


async def analyze_expense_pattern(
    national_id: str,
    declared_obligations: float,
) -> ExpenseAnalysis:
    """
    Analiza el patrón de gastos del solicitante a través del bureau y registros bancarios.
    """
    id_hash = hashlib.sha256(national_id.encode()).hexdigest()

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            bureau_url = os.environ.get("CREDIT_BUREAU_URL", "http://localhost:9004")
            response = await client.post(
                f"{bureau_url}/api/v2/expenses/analyze",
                json={"national_id_hash": id_hash, "declared_obligations": declared_obligations},
                headers={"Authorization": f"Bearer {os.environ.get('CREDIT_BUREAU_API_KEY', '')}"},
            )
            response.raise_for_status()
            data = response.json()

            bureau_obligations = float(data.get("total_obligations", declared_obligations))
            discrepancy = abs(declared_obligations - bureau_obligations) / max(declared_obligations, 1)

            return ExpenseAnalysis(
                total_monthly_obligations=bureau_obligations,
                rent_or_mortgage=float(data.get("rent_mortgage", 0.0)),
                existing_loans=float(data.get("existing_loans", 0.0)),
                credit_card_minimums=float(data.get("credit_card_minimums", 0.0)),
                other_obligations=float(data.get("other", 0.0)),
                declared_vs_bureau_discrepancy=discrepancy,
            )
    except Exception as e:
        logger.warning("analyze_expense_pattern.failed", error=str(e))
        return ExpenseAnalysis(
            total_monthly_obligations=declared_obligations,
            other_obligations=declared_obligations,
            declared_vs_bureau_discrepancy=0.0,
        )
