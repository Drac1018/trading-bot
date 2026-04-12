from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PauseRecoveryClass = Literal[
    "recoverable_system",
    "manual_pause",
    "hard_risk_lock",
    "config_block",
    "portfolio_unsafe",
    "unknown",
]
PauseSeverity = Literal["info", "warning", "critical"]


@dataclass(frozen=True, slots=True)
class PauseReasonPolicy:
    code: str
    recovery_class: PauseRecoveryClass
    severity: PauseSeverity
    auto_resume_eligible: bool
    description: str


UNKNOWN_PAUSE_POLICY = PauseReasonPolicy(
    code="UNKNOWN",
    recovery_class="unknown",
    severity="warning",
    auto_resume_eligible=False,
    description="Unknown pause reason. Do not auto resume.",
)


PAUSE_REASON_POLICIES: dict[str, PauseReasonPolicy] = {
    "EXCHANGE_ACCOUNT_STATE_UNAVAILABLE": PauseReasonPolicy(
        code="EXCHANGE_ACCOUNT_STATE_UNAVAILABLE",
        recovery_class="recoverable_system",
        severity="warning",
        auto_resume_eligible=True,
        description="Exchange account state could not be loaded.",
    ),
    "EXCHANGE_CONNECTIVITY_TEMPORARY_FAILURE": PauseReasonPolicy(
        code="EXCHANGE_CONNECTIVITY_TEMPORARY_FAILURE",
        recovery_class="recoverable_system",
        severity="warning",
        auto_resume_eligible=True,
        description="Temporary exchange or network connectivity degradation.",
    ),
    "TEMPORARY_MARKET_DATA_FAILURE": PauseReasonPolicy(
        code="TEMPORARY_MARKET_DATA_FAILURE",
        recovery_class="recoverable_system",
        severity="warning",
        auto_resume_eligible=True,
        description="Temporary market data failure or stale market feed.",
    ),
    "TEMPORARY_SYNC_FAILURE": PauseReasonPolicy(
        code="TEMPORARY_SYNC_FAILURE",
        recovery_class="recoverable_system",
        severity="warning",
        auto_resume_eligible=True,
        description="Temporary live state sync failure.",
    ),
    "EXCHANGE_POSITION_SYNC_FAILED": PauseReasonPolicy(
        code="EXCHANGE_POSITION_SYNC_FAILED",
        recovery_class="recoverable_system",
        severity="warning",
        auto_resume_eligible=True,
        description="Exchange position sync failed.",
    ),
    "EXCHANGE_OPEN_ORDERS_SYNC_FAILED": PauseReasonPolicy(
        code="EXCHANGE_OPEN_ORDERS_SYNC_FAILED",
        recovery_class="recoverable_system",
        severity="warning",
        auto_resume_eligible=True,
        description="Exchange open order sync failed.",
    ),
    "MANUAL_USER_REQUEST": PauseReasonPolicy(
        code="MANUAL_USER_REQUEST",
        recovery_class="manual_pause",
        severity="warning",
        auto_resume_eligible=False,
        description="Manual operator pause.",
    ),
    "DAILY_LOSS_LIMIT_REACHED": PauseReasonPolicy(
        code="DAILY_LOSS_LIMIT_REACHED",
        recovery_class="hard_risk_lock",
        severity="critical",
        auto_resume_eligible=False,
        description="Daily loss limit reached.",
    ),
    "HARD_RISK_LOCK_DAILY_LOSS": PauseReasonPolicy(
        code="HARD_RISK_LOCK_DAILY_LOSS",
        recovery_class="hard_risk_lock",
        severity="critical",
        auto_resume_eligible=False,
        description="Daily loss hard lock.",
    ),
    "MAX_CONSECUTIVE_LOSSES_REACHED": PauseReasonPolicy(
        code="MAX_CONSECUTIVE_LOSSES_REACHED",
        recovery_class="hard_risk_lock",
        severity="critical",
        auto_resume_eligible=False,
        description="Maximum consecutive losses reached.",
    ),
    "HARD_RISK_LOCK_CONSECUTIVE_LOSS": PauseReasonPolicy(
        code="HARD_RISK_LOCK_CONSECUTIVE_LOSS",
        recovery_class="hard_risk_lock",
        severity="critical",
        auto_resume_eligible=False,
        description="Consecutive loss hard lock.",
    ),
    "PROTECTIVE_ORDER_FAILURE": PauseReasonPolicy(
        code="PROTECTIVE_ORDER_FAILURE",
        recovery_class="portfolio_unsafe",
        severity="critical",
        auto_resume_eligible=False,
        description="Protective order creation failed.",
    ),
    "MISSING_PROTECTIVE_ORDERS": PauseReasonPolicy(
        code="MISSING_PROTECTIVE_ORDERS",
        recovery_class="portfolio_unsafe",
        severity="critical",
        auto_resume_eligible=False,
        description="Open position is missing protective orders.",
    ),
    "PORTFOLIO_RISK_UNCERTAIN": PauseReasonPolicy(
        code="PORTFOLIO_RISK_UNCERTAIN",
        recovery_class="portfolio_unsafe",
        severity="critical",
        auto_resume_eligible=False,
        description="Portfolio risk state is uncertain.",
    ),
    "ACCOUNT_STATE_INCONSISTENT": PauseReasonPolicy(
        code="ACCOUNT_STATE_INCONSISTENT",
        recovery_class="portfolio_unsafe",
        severity="critical",
        auto_resume_eligible=False,
        description="Exchange and local account state are inconsistent.",
    ),
    "LIVE_CREDENTIALS_MISSING": PauseReasonPolicy(
        code="LIVE_CREDENTIALS_MISSING",
        recovery_class="config_block",
        severity="critical",
        auto_resume_eligible=False,
        description="Live exchange credentials are missing.",
    ),
    "LIVE_ENV_DISABLED": PauseReasonPolicy(
        code="LIVE_ENV_DISABLED",
        recovery_class="config_block",
        severity="warning",
        auto_resume_eligible=False,
        description="Live trading is disabled by environment policy.",
    ),
    "LIVE_TRADING_DISABLED": PauseReasonPolicy(
        code="LIVE_TRADING_DISABLED",
        recovery_class="config_block",
        severity="warning",
        auto_resume_eligible=False,
        description="Live trading is disabled in settings.",
    ),
    "LIVE_APPROVAL_POLICY_DISABLED": PauseReasonPolicy(
        code="LIVE_APPROVAL_POLICY_DISABLED",
        recovery_class="config_block",
        severity="warning",
        auto_resume_eligible=False,
        description="Manual approval policy is disabled.",
    ),
    "LIVE_APPROVAL_REQUIRED": PauseReasonPolicy(
        code="LIVE_APPROVAL_REQUIRED",
        recovery_class="config_block",
        severity="warning",
        auto_resume_eligible=False,
        description="Live execution approval is not currently armed.",
    ),
}


def get_pause_reason_policy(reason_code: str | None) -> PauseReasonPolicy:
    if not reason_code:
        return UNKNOWN_PAUSE_POLICY
    return PAUSE_REASON_POLICIES.get(reason_code, UNKNOWN_PAUSE_POLICY)


def pause_reason_allows_auto_resume(reason_code: str | None) -> bool:
    return get_pause_reason_policy(reason_code).auto_resume_eligible


def pause_reason_recovery_class(reason_code: str | None) -> PauseRecoveryClass:
    return get_pause_reason_policy(reason_code).recovery_class


def pause_reason_severity(reason_code: str | None) -> PauseSeverity:
    return get_pause_reason_policy(reason_code).severity
