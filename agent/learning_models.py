"""Data models for learning-related functionality."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class LearningGoalStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    PAUSED = "paused"
    ABANDONED = "abandoned"


class LearningGoalType(str, Enum):
    SKILL = "skill"
    KNOWLEDGE = "knowledge"
    CERTIFICATION = "certification"
    PROJECT = "project"


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class TaskDifficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class ReviewStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    SCHEDULED = "scheduled"


@dataclass
class LearningGoal:
    id: str
    name: str
    description: str
    goal_type: LearningGoalType
    status: LearningGoalStatus
    target_date: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    estimated_hours: float
    progress: float = 0.0
    tags: list[str] = field(default_factory=list)
    prerequisites: list[str] = field(default_factory=list)


@dataclass
class LearningPlan:
    id: str
    goal_id: str
    name: str
    description: str
    created_at: datetime
    updated_at: datetime
    total_tasks: int = 0
    completed_tasks: int = 0


@dataclass
class DailyTask:
    id: str
    plan_id: str
    goal_id: str
    title: str
    description: str
    status: TaskStatus
    difficulty: TaskDifficulty
    estimated_minutes: int
    scheduled_date: datetime
    completed_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    position: int = 0
    priority: int = 3


@dataclass
class LearningSession:
    id: str
    goal_id: str
    task_id: Optional[str]
    start_time: datetime
    end_time: Optional[datetime]
    duration_minutes: int = 0
    notes: str = ""
    focus_score: float = 0.0


@dataclass
class ReviewSession:
    id: str
    goal_id: str
    scheduled_date: datetime
    completed_date: Optional[datetime]
    status: ReviewStatus
    created_at: datetime
    updated_at: datetime


@dataclass
class LearningProgress:
    goal_id: str
    task_id: str
    user_id: str
    completed_at: datetime
    time_spent_minutes: int
    notes: str = ""


@dataclass
class KnowledgeMastery:
    concept_id: str
    concept_name: str
    goal_id: str
    mastery_level: float
    last_assessed_at: datetime
    next_review_at: datetime


@dataclass
class LearningStats:
    total_goals: int
    completed_goals: int
    active_goals: int
    total_hours_learned: float
    total_tasks_completed: int
    streak_days: int
    average_daily_minutes: float


@dataclass
class LearningRecommendation:
    id: str
    goal_id: Optional[str]
    type: str
    content: str
    reason: str
    priority: int
    created_at: datetime