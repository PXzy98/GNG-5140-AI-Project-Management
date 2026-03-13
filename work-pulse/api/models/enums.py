from enum import Enum


class ArtifactType(str, Enum):
    EMAIL = "email"
    MEETING_MINUTES = "meeting_minutes"
    STATUS_REPORT = "status_report"
    SOW = "sow"
    NOTES = "notes"
    OTHER = "other"


class TaskStatus(str, Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    ROLLED_OVER = "rolled_over"


class PriorityLevel(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RiskCategory(str, Enum):
    SCHEDULE = "schedule"
    BUDGET = "budget"
    TECHNICAL = "technical"
    RESOURCE = "resource"
    COMPLIANCE = "compliance"


class RiskLevel(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class DriftType(str, Enum):
    WITHIN_SCOPE = "within_scope"
    NEW_REQUIREMENT = "new_requirement"
    SCOPE_EXPANSION = "scope_expansion"
    CONTRADICTS_SCOPE = "contradicts_scope"
    AMBIGUOUS = "ambiguous"


class AlertSeverity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AlertStatus(str, Enum):
    OPEN = "open"
    RESOLVED = "resolved"
    FALSE_POSITIVE = "false_positive"


class AnalysisDepth(str, Enum):
    SHALLOW = "shallow"
    STANDARD = "standard"
    DEEP = "deep"
