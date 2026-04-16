from enum import StrEnum


class AgentRole(StrEnum):
    CHIEF_REVIEW = "chief_review"
    INTEGRATION_PLANNER = "integration_planner"
    TRADING_DECISION = "trading_decision"
    UI_UX = "ui_ux"


class DecisionType(StrEnum):
    HOLD = "hold"
    LONG = "long"
    SHORT = "short"
    REDUCE = "reduce"
    EXIT = "exit"


class OperatingMode(StrEnum):
    HOLD = "hold"
    MONITOR = "monitor"
    ACT = "act"


class PriorityLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class SeverityLevel(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class OrderStatus(StrEnum):
    PENDING = "pending"
    OPEN = "open"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class PositionStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class SchedulerStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    RUNNING = "running"


class TriggerEvent(StrEnum):
    REALTIME = "realtime_cycle"
    POST_DECISION = "post_decision_review"
    SCHEDULED = "scheduled_review"
    REPLAY = "historical_replay"
    MANUAL = "manual"
