from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ConfidenceLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    TO_CONFIRM = "to_confirm"


class Applicability(str, Enum):
    APPLICABLE = "applicable"
    NOT_APPLICABLE = "not_applicable"
    TO_CONFIRM = "to_confirm"


@dataclass
class AggressionFilter:
    item: str
    category: str
    status: Applicability
    justification: str
    point_to_validate: bool = False


@dataclass
class RiskScenario:
    id: str
    function_element: str
    life_phase: str
    aggression_threat: str
    dangerous_situation: str
    feared_event: str
    plausible_causes: list[str] = field(default_factory=list)
    consequences: list[str] = field(default_factory=list)
    existing_barriers: list[str] = field(default_factory=list)
    recommended_barriers: list[str] = field(default_factory=list)
    severity: Optional[str] = None
    likelihood: Optional[str] = None
    risk_level: Optional[str] = None
    justification: str = ""
    confidence: ConfidenceLevel = ConfidenceLevel.TO_CONFIRM
    points_to_validate: list[str] = field(default_factory=list)


@dataclass
class AnalysisState:
    project_name: str = ""
    system_description: str = ""
    perimeter_limits: str = ""
    life_phases: list[str] = field(default_factory=list)
    main_functions: list[str] = field(default_factory=list)
    interfaces: list[str] = field(default_factory=list)
    users_operators: list[str] = field(default_factory=list)
    environmental_conditions: str = ""
    key_hypotheses: list[str] = field(default_factory=list)

    aggression_filters: list[AggressionFilter] = field(default_factory=list)
    threat_filters: list[AggressionFilter] = field(default_factory=list)

    risk_scenarios: list[RiskScenario] = field(default_factory=list)

    quality_issues: list[str] = field(default_factory=list)
    open_points: list[str] = field(default_factory=list)
    missing_data: list[str] = field(default_factory=list)
    decisions_required: list[str] = field(default_factory=list)

    current_step: str = "cadrage"
    checkpoint_validated: bool = False
    human_feedback: str = ""

    def to_dict(self) -> dict:
        return {
            "project_name": self.project_name,
            "system_description": self.system_description,
            "perimeter_limits": self.perimeter_limits,
            "life_phases": self.life_phases,
            "main_functions": self.main_functions,
            "interfaces": self.interfaces,
            "key_hypotheses": self.key_hypotheses,
            "aggression_filters": [
                {"item": a.item, "category": a.category, "status": a.status.value, "justification": a.justification}
                for a in self.aggression_filters
            ],
            "threat_filters": [
                {"item": t.item, "category": t.category, "status": t.status.value, "justification": t.justification}
                for t in self.threat_filters
            ],
            "risk_scenarios_count": len(self.risk_scenarios),
            "quality_issues": self.quality_issues,
            "open_points": self.open_points,
            "current_step": self.current_step,
        }
