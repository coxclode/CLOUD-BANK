"""
CLOUD BANK — Herramientas del Agente Actuario
Modelos estadísticos de probabilidad de impago:
  - Regresión Logística (interpretable, regulatoriamente justificable)
  - Gradient Boosting (alta precisión)
  - Red Neuronal (captura patrones no lineales)
  - Ensemble ponderado
  - SHAP values para explicabilidad
  - Métricas de pérdida esperada
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import structlog

logger = structlog.get_logger(__name__)


# ─── Coeficientes del modelo de regresión logística ──────────────────────────
# Entrenados offline sobre cartera histórica de CLOUD BANK (simulados aquí)

_LR_COEFFICIENTS: dict[str, float] = {
    "intercept":              -2.1,
    "credit_score":           -0.004,
    "credit_utilization":      1.8,
    "delinquent_accounts":     0.5,
    "oldest_account_months":  -0.005,
    "payment_history_score":  -2.0,
    "bankruptcy_history":      2.5,
    "post_credit_dti":         3.0,
    "capacity_score":         -1.5,
    "income_stability":       -1.0,
    "employment_months":      -0.008,
    "fraud_score":             3.5,
    "amount_to_income_ratio":  0.4,
}

_GBM_WEIGHTS: dict[str, float] = {
    "credit_score":           -0.006,
    "credit_utilization":      2.2,
    "delinquent_accounts":     0.65,
    "post_credit_dti":         3.8,
    "payment_history_score":  -2.5,
    "fraud_score":             4.0,
    "income_stability":       -1.2,
    "capacity_score":         -2.0,
}

_ENSEMBLE_WEIGHTS = {"lr": 0.30, "gbm": 0.45, "nn": 0.25}


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-500, min(500, x))))


async def run_logistic_regression_model(features: dict[str, float]) -> float:
    """
    Modelo de regresión logística. Linealmente interpretable.
    Conforme a requisitos regulatorios de explicabilidad (Basel III / SR 11-7).
    """
    log_odds = _LR_COEFFICIENTS["intercept"]
    for feature, coef in _LR_COEFFICIENTS.items():
        if feature != "intercept":
            log_odds += coef * features.get(feature, 0.0)
    pd = _sigmoid(log_odds)
    logger.debug("lr_model.computed", pd=round(pd, 4))
    return round(pd, 4)


async def run_gradient_boosting_model(features: dict[str, float]) -> float:
    """
    Gradient Boosting simulado. En producción: XGBoost / LightGBM entrenado.
    Mayor precisión predictiva que LR, especialmente en relaciones no lineales.
    """
    raw_score = 0.0
    for feature, weight in _GBM_WEIGHTS.items():
        val = features.get(feature, 0.0)
        # GBM captura interacciones — simulamos con transformaciones no lineales
        raw_score += weight * val + 0.1 * weight * (val ** 2)

    # Ajuste de baseline
    raw_score += -1.8

    pd = _sigmoid(raw_score)

    # Añadir pequeña perturbación para simular diferencia entre modelos
    noise = (features.get("credit_score", 650) % 7) / 1000
    pd = max(0.001, min(0.999, pd + noise))

    logger.debug("gbm_model.computed", pd=round(pd, 4))
    return round(pd, 4)


async def run_neural_network_model(features: dict[str, float]) -> float:
    """
    Red neuronal simulada (2 capas ocultas). En producción: PyTorch/TensorFlow.
    Captura patrones complejos no detectados por modelos lineales.
    """
    # Capa 1: transformación no lineal
    inputs = np.array([
        features.get("credit_score", 650) / 1000,
        features.get("post_credit_dti", 0.3),
        features.get("fraud_score", 0.0),
        features.get("payment_history_score", 0.8),
        features.get("income_stability", 0.7),
        features.get("credit_utilization", 0.3),
        features.get("capacity_score", 0.7),
        features.get("employment_months", 24) / 60,
    ])

    # Pesos simulados (en producción: cargar desde modelo serializado)
    W1 = np.array([
        [-0.8,  0.3,  1.5, -0.9,  -0.7,  0.8, -0.6,  -0.5],
        [ 0.5,  0.9,  0.8,  0.4,   0.3,  0.5,  0.4,   0.3],
        [-0.6,  1.2,  2.0, -1.1,  -0.9,  1.1, -0.8,  -0.6],
    ])
    b1 = np.array([-0.5, 0.2, -0.3])

    # Capa oculta 1 (ReLU)
    h1 = np.maximum(0, W1 @ inputs + b1)

    # Capa 2
    W2 = np.array([1.2, -0.8, 1.5])
    b2 = -0.9
    output = float(W2 @ h1 + b2)

    pd = _sigmoid(output)
    pd = max(0.001, min(0.999, pd))

    logger.debug("nn_model.computed", pd=round(pd, 4))
    return round(pd, 4)


async def compute_ensemble_score(lr_pd: float, gbm_pd: float, nn_pd: float) -> float:
    """
    Combina los 3 modelos con pesos optimizados por validación cruzada histórica.
    """
    ensemble = (
        _ENSEMBLE_WEIGHTS["lr"]  * lr_pd +
        _ENSEMBLE_WEIGHTS["gbm"] * gbm_pd +
        _ENSEMBLE_WEIGHTS["nn"]  * nn_pd
    )
    logger.debug(
        "ensemble.computed",
        lr=round(lr_pd, 4),
        gbm=round(gbm_pd, 4),
        nn=round(nn_pd, 4),
        ensemble=round(ensemble, 4),
    )
    return round(ensemble, 4)


async def compute_shap_values(features: dict[str, float]) -> dict[str, float]:
    """
    Calcula SHAP values aproximados para explicabilidad regulatoria.
    En producción: shap.TreeExplainer o shap.KernelExplainer sobre el modelo real.
    """
    baseline_pd = 0.20

    shap: dict[str, float] = {}
    for feature, value in features.items():
        coef = _LR_COEFFICIENTS.get(feature, 0.0)
        shap[feature] = round(coef * value * (1 - baseline_pd), 4)

    # Normalizar para que sumen aproximadamente la desviación del baseline
    total = sum(abs(v) for v in shap.values())
    if total > 0:
        shap = {k: round(v / total * 0.5, 4) for k, v in shap.items()}

    return shap


async def estimate_loss_metrics(
    pd: float,
    ead: float,
    lgd: float = 0.45,
) -> dict[str, float]:
    """
    Calcula métricas de pérdida esperada bajo Basel III:
    EL = PD × LGD × EAD
    """
    el = pd * lgd * ead
    unexpected_loss = ead * lgd * math.sqrt(pd * (1 - pd)) * 1.96
    return {
        "expected_loss": round(el, 2),
        "loss_given_default": lgd,
        "exposure_at_default": ead,
        "unexpected_loss_95": round(unexpected_loss, 2),
        "risk_weighted_assets": round(ead * pd * lgd * 12.5, 2),
    }
