"""Learning-related REST API endpoints for HermesLearn."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from hermes_cli.config import get_hermes_home
from agent.learning_models import (
    DailyTask,
    ErrorType,
    LearningGoal,
    LearningGoalStatus,
    LearningGoalType,
    LearningPlan,
    LearningStats,
    MasteryStatus,
    TaskDifficulty,
    TaskStatus,
)

router = APIRouter(prefix="/api/learning", tags=["learning"])

_LEARNING_DATA_DIR = get_hermes_home() / "learning"
_LEARNING_DATA_DIR.mkdir(exist_ok=True)


class CreateLearningGoalRequest(BaseModel):
    name: str
    description: str
    goal_type: str = Field(..., description="skill, knowledge, certification, or project")
    target_date: Optional[str] = None
    estimated_hours: float = 10.0
    tags: List[str] = []


class CreateLearningPlanRequest(BaseModel):
    goal_id: str
    name: str
    description: str = ""


class CreateDailyTaskRequest(BaseModel):
    plan_id: str
    goal_id: str
    title: str
    description: str = ""
    difficulty: str = "medium"
    estimated_minutes: int = 30
    scheduled_date: str
    priority: int = 3


class UpdateTaskStatusRequest(BaseModel):
    status: str = "completed"


def _generate_id() -> str:
    import secrets

    return secrets.token_urlsafe(16)


def _load_goals() -> Dict[str, Dict[str, Any]]:
    path = _LEARNING_DATA_DIR / "goals.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save_goals(goals: Dict[str, Dict[str, Any]]) -> None:
    path = _LEARNING_DATA_DIR / "goals.json"
    path.write_text(json.dumps(goals, indent=2), encoding="utf-8")


def _load_plans() -> Dict[str, Dict[str, Any]]:
    path = _LEARNING_DATA_DIR / "plans.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save_plans(plans: Dict[str, Dict[str, Any]]) -> None:
    path = _LEARNING_DATA_DIR / "plans.json"
    path.write_text(json.dumps(plans, indent=2), encoding="utf-8")


def _load_tasks() -> Dict[str, Dict[str, Any]]:
    path = _LEARNING_DATA_DIR / "tasks.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save_tasks(tasks: Dict[str, Dict[str, Any]]) -> None:
    path = _LEARNING_DATA_DIR / "tasks.json"
    path.write_text(json.dumps(tasks, indent=2), encoding="utf-8")


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


@router.get("/goals", response_model=List[Dict[str, Any]])
async def get_learning_goals(request: Request):
    goals = _load_goals()
    return list(goals.values())


@router.post("/goals", response_model=Dict[str, Any])
async def create_learning_goal(request: Request, body: CreateLearningGoalRequest):
    goals = _load_goals()

    try:
        goal_type = LearningGoalType(body.goal_type)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid goal type")

    now = datetime.now(timezone.utc)
    goal_id = _generate_id()

    goal: Dict[str, Any] = {
        "id": goal_id,
        "name": body.name,
        "description": body.description,
        "goal_type": body.goal_type,
        "status": "active",
        "target_date": body.target_date,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "estimated_hours": body.estimated_hours,
        "progress": 0.0,
        "tags": body.tags,
        "prerequisites": [],
    }

    goals[goal_id] = goal
    _save_goals(goals)

    return goal


@router.get("/goals/{goal_id}", response_model=Dict[str, Any])
async def get_learning_goal(request: Request, goal_id: str):
    goals = _load_goals()
    if goal_id not in goals:
        raise HTTPException(status_code=404, detail="Goal not found")
    return goals[goal_id]


@router.put("/goals/{goal_id}", response_model=Dict[str, Any])
async def update_learning_goal(request: Request, goal_id: str, body: CreateLearningGoalRequest):
    goals = _load_goals()
    if goal_id not in goals:
        raise HTTPException(status_code=404, detail="Goal not found")

    goals[goal_id].update(
        {
            "name": body.name,
            "description": body.description,
            "goal_type": body.goal_type,
            "target_date": body.target_date,
            "estimated_hours": body.estimated_hours,
            "tags": body.tags,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    _save_goals(goals)

    return goals[goal_id]


@router.delete("/goals/{goal_id}")
async def delete_learning_goal(request: Request, goal_id: str):
    goals = _load_goals()
    if goal_id not in goals:
        raise HTTPException(status_code=404, detail="Goal not found")

    plans = _load_plans()
    plan_ids_to_delete = [pid for pid, p in plans.items() if p["goal_id"] == goal_id]
    for pid in plan_ids_to_delete:
        del plans[pid]
    _save_plans(plans)

    tasks = _load_tasks()
    remaining_tasks = {tid: t for tid, t in tasks.items() if t["goal_id"] != goal_id}
    _save_tasks(remaining_tasks)

    del goals[goal_id]
    _save_goals(goals)

    return {"message": "Goal deleted successfully"}


@router.post("/goals/{goal_id}/status", response_model=Dict[str, Any])
async def update_goal_status(request: Request, goal_id: str, status: str):
    goals = _load_goals()
    if goal_id not in goals:
        raise HTTPException(status_code=404, detail="Goal not found")

    try:
        LearningGoalStatus(status)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid status")

    goals[goal_id]["status"] = status
    goals[goal_id]["updated_at"] = datetime.now(timezone.utc).isoformat()

    if status == "completed":
        goals[goal_id]["progress"] = 100.0

    _save_goals(goals)
    return goals[goal_id]


@router.get("/plans", response_model=List[Dict[str, Any]])
async def get_learning_plans(request: Request):
    plans = _load_plans()
    return list(plans.values())


@router.get("/goals/{goal_id}/plans", response_model=List[Dict[str, Any]])
async def get_goal_plans(request: Request, goal_id: str):
    plans = _load_plans()
    return [p for p in plans.values() if p["goal_id"] == goal_id]


@router.post("/plans", response_model=Dict[str, Any])
async def create_learning_plan(request: Request, body: CreateLearningPlanRequest):
    goals = _load_goals()
    if body.goal_id not in goals:
        raise HTTPException(status_code=404, detail="Goal not found")

    plans = _load_plans()
    now = datetime.now(timezone.utc)
    plan_id = _generate_id()

    plan: Dict[str, Any] = {
        "id": plan_id,
        "goal_id": body.goal_id,
        "name": body.name,
        "description": body.description,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "total_tasks": 0,
        "completed_tasks": 0,
    }

    plans[plan_id] = plan
    _save_plans(plans)

    return plan


@router.get("/plans/{plan_id}", response_model=Dict[str, Any])
async def get_learning_plan(request: Request, plan_id: str):
    plans = _load_plans()
    if plan_id not in plans:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plans[plan_id]


@router.delete("/plans/{plan_id}")
async def delete_learning_plan(request: Request, plan_id: str):
    plans = _load_plans()
    if plan_id not in plans:
        raise HTTPException(status_code=404, detail="Plan not found")

    tasks = _load_tasks()
    remaining_tasks = {tid: t for tid, t in tasks.items() if t["plan_id"] != plan_id}
    _save_tasks(remaining_tasks)

    del plans[plan_id]
    _save_plans(plans)

    return {"message": "Plan deleted successfully"}


@router.get("/tasks", response_model=List[Dict[str, Any]])
async def get_daily_tasks(request: Request, plan_id: Optional[str] = None):
    tasks = _load_tasks()
    if plan_id:
        return [t for t in tasks.values() if t["plan_id"] == plan_id]
    return list(tasks.values())


@router.get("/goals/{goal_id}/tasks", response_model=List[Dict[str, Any]])
async def get_goal_tasks(request: Request, goal_id: str):
    tasks = _load_tasks()
    return [t for t in tasks.values() if t["goal_id"] == goal_id]


@router.post("/tasks", response_model=Dict[str, Any])
async def create_daily_task(request: Request, body: CreateDailyTaskRequest):
    plans = _load_plans()
    if body.plan_id not in plans:
        raise HTTPException(status_code=404, detail="Plan not found")

    goals = _load_goals()
    if body.goal_id not in goals:
        raise HTTPException(status_code=404, detail="Goal not found")

    try:
        TaskDifficulty(body.difficulty)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid difficulty")

    tasks = _load_tasks()
    now = datetime.now(timezone.utc)
    task_id = _generate_id()

    task: Dict[str, Any] = {
        "id": task_id,
        "plan_id": body.plan_id,
        "goal_id": body.goal_id,
        "title": body.title,
        "description": body.description,
        "status": "pending",
        "difficulty": body.difficulty,
        "estimated_minutes": body.estimated_minutes,
        "scheduled_date": body.scheduled_date,
        "completed_at": None,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "position": len(tasks),
        "priority": body.priority,
    }

    tasks[task_id] = task
    _save_tasks(tasks)

    plans[body.plan_id]["total_tasks"] += 1
    plans[body.plan_id]["updated_at"] = now.isoformat()
    _save_plans(plans)

    return task


@router.get("/tasks/{task_id}", response_model=Dict[str, Any])
async def get_daily_task(request: Request, task_id: str):
    tasks = _load_tasks()
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    return tasks[task_id]


@router.post("/tasks/{task_id}/status", response_model=Dict[str, Any])
async def update_task_status(request: Request, task_id: str, body: UpdateTaskStatusRequest):
    tasks = _load_tasks()
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")

    try:
        TaskStatus(body.status)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid status")

    now = datetime.now(timezone.utc)
    tasks[task_id]["status"] = body.status
    tasks[task_id]["updated_at"] = now.isoformat()

    if body.status == "completed":
        tasks[task_id]["completed_at"] = now.isoformat()

        plans = _load_plans()
        plan_id = tasks[task_id]["plan_id"]
        if plan_id in plans:
            plans[plan_id]["completed_tasks"] += 1
            plans[plan_id]["updated_at"] = now.isoformat()
            _save_plans(plans)

        goals = _load_goals()
        goal_id = tasks[task_id]["goal_id"]
        if goal_id in goals:
            goal_tasks = [t for t in tasks.values() if t["goal_id"] == goal_id]
            completed = sum(1 for t in goal_tasks if t["status"] == "completed")
            total = len(goal_tasks)
            if total > 0:
                goals[goal_id]["progress"] = round((completed / total) * 100, 1)
            goals[goal_id]["updated_at"] = now.isoformat()
            _save_goals(goals)

    _save_tasks(tasks)
    return tasks[task_id]


@router.delete("/tasks/{task_id}")
async def delete_daily_task(request: Request, task_id: str):
    tasks = _load_tasks()
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")

    plan_id = tasks[task_id]["plan_id"]
    del tasks[task_id]
    _save_tasks(tasks)

    plans = _load_plans()
    if plan_id in plans:
        plans[plan_id]["total_tasks"] -= 1
        if plans[plan_id]["completed_tasks"] > plans[plan_id]["total_tasks"]:
            plans[plan_id]["completed_tasks"] = plans[plan_id]["total_tasks"]
        plans[plan_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
        _save_plans(plans)

    return {"message": "Task deleted successfully"}


@router.get("/stats", response_model=Dict[str, Any])
async def get_learning_stats(request: Request):
    goals = _load_goals()
    tasks = _load_tasks()

    total_goals = len(goals)
    completed_goals = sum(1 for g in goals.values() if g["status"] == "completed")
    active_goals = sum(1 for g in goals.values() if g["status"] == "active")

    completed_tasks = sum(1 for t in tasks.values() if t["status"] == "completed")
    total_minutes = sum(
        t["estimated_minutes"] for t in tasks.values() if t["status"] == "completed"
    )
    total_hours = round(total_minutes / 60, 1)

    today = datetime.now(timezone.utc).date()
    today_tasks = [
        t for t in tasks.values() if _parse_datetime(t["scheduled_date"]).date() == today
    ]

    stats: Dict[str, Any] = {
        "total_goals": total_goals,
        "completed_goals": completed_goals,
        "active_goals": active_goals,
        "total_hours_learned": total_hours,
        "total_tasks_completed": completed_tasks,
        "streak_days": 0,
        "average_daily_minutes": 0,
        "today_tasks": len(today_tasks),
        "today_completed": sum(1 for t in today_tasks if t["status"] == "completed"),
    }

    return stats


@router.get("/today", response_model=Dict[str, Any])
async def get_today_tasks(request: Request):
    tasks = _load_tasks()
    today = datetime.now(timezone.utc).date()

    today_tasks = [
        t
        for t in tasks.values()
        if _parse_datetime(t["scheduled_date"]).date() == today
    ]

    today_tasks.sort(key=lambda t: t.get("priority", 3))

    goals = _load_goals()

    return {
        "date": today.isoformat(),
        "tasks": today_tasks,
        "goal_names": {g["id"]: g["name"] for g in goals.values()},
    }


class GeneratePlanRequest(BaseModel):
    topic: str


@router.post("/generate", response_model=Dict[str, Any])
async def generate_learning_plan(request: Request, body: GeneratePlanRequest):
    topic = body.topic.strip()
    if not topic:
        raise HTTPException(status_code=400, detail="Topic is required")

    now = datetime.now(timezone.utc)
    today = now.date()

    GOAL_TEMPLATES = {
        "python": {
            "name": "学习 Python 编程",
            "description": "从零基础到熟练掌握 Python 编程，包括基础语法、面向对象编程、常用库和项目实践",
            "goal_type": "skill",
            "estimated_hours": 80,
            "plans": [
                {
                    "name": "Python 基础入门",
                    "description": "学习 Python 基础语法、数据类型、控制流等",
                    "tasks": [
                        {"title": "安装 Python 环境", "minutes": 30, "priority": 1},
                        {"title": "学习变量和数据类型", "minutes": 60, "priority": 1},
                        {"title": "学习条件语句和循环", "minutes": 60, "priority": 1},
                        {"title": "学习函数定义和调用", "minutes": 60, "priority": 1},
                        {"title": "练习：编写第一个 Python 程序", "minutes": 45, "priority": 2},
                    ],
                },
                {
                    "name": "面向对象编程",
                    "description": "学习类和对象、继承、封装、多态等面向对象概念",
                    "tasks": [
                        {"title": "学习类和对象的概念", "minutes": 60, "priority": 1},
                        {"title": "学习继承和多态", "minutes": 90, "priority": 1},
                        {"title": "学习封装和访问控制", "minutes": 60, "priority": 2},
                        {"title": "练习：创建一个类并实现继承", "minutes": 60, "priority": 2},
                    ],
                },
                {
                    "name": "常用库和实战",
                    "description": "学习常用 Python 库并进行项目实践",
                    "tasks": [
                        {"title": "学习 NumPy 数组操作", "minutes": 90, "priority": 1},
                        {"title": "学习 Pandas 数据分析", "minutes": 120, "priority": 1},
                        {"title": "学习 Matplotlib 数据可视化", "minutes": 90, "priority": 2},
                        {"title": "实战项目：数据处理和分析", "minutes": 180, "priority": 1},
                    ],
                },
            ],
        },
        "javascript": {
            "name": "学习 JavaScript 编程",
            "description": "掌握 JavaScript 语言基础和现代前端开发技术",
            "goal_type": "skill",
            "estimated_hours": 60,
            "plans": [
                {
                    "name": "JavaScript 基础",
                    "description": "学习 JS 基础语法、DOM 操作、事件处理",
                    "tasks": [
                        {"title": "学习变量、数据类型和运算符", "minutes": 60, "priority": 1},
                        {"title": "学习条件语句和循环", "minutes": 60, "priority": 1},
                        {"title": "学习函数和作用域", "minutes": 90, "priority": 1},
                        {"title": "学习 DOM 操作", "minutes": 90, "priority": 1},
                        {"title": "练习：创建交互式网页", "minutes": 60, "priority": 2},
                    ],
                },
                {
                    "name": "ES6+ 新特性",
                    "description": "学习现代 JavaScript 语法和特性",
                    "tasks": [
                        {"title": "学习箭头函数和模板字符串", "minutes": 60, "priority": 1},
                        {"title": "学习解构赋值和扩展运算符", "minutes": 60, "priority": 1},
                        {"title": "学习 Promise 和异步编程", "minutes": 90, "priority": 1},
                        {"title": "学习 async/await", "minutes": 60, "priority": 1},
                    ],
                },
            ],
        },
        "react": {
            "name": "学习 React 框架",
            "description": "掌握 React 组件开发、状态管理和路由",
            "goal_type": "skill",
            "estimated_hours": 50,
            "plans": [
                {
                    "name": "React 基础",
                    "description": "学习组件、Props、State 和生命周期",
                    "tasks": [
                        {"title": "搭建 React 开发环境", "minutes": 30, "priority": 1},
                        {"title": "学习函数组件和 JSX", "minutes": 60, "priority": 1},
                        {"title": "学习 Props 和 State", "minutes": 90, "priority": 1},
                        {"title": "学习 Hooks (useState, useEffect)", "minutes": 90, "priority": 1},
                        {"title": "练习：创建 Todo 应用", "minutes": 120, "priority": 1},
                    ],
                },
                {
                    "name": "React 进阶",
                    "description": "学习路由、状态管理和性能优化",
                    "tasks": [
                        {"title": "学习 React Router", "minutes": 60, "priority": 1},
                        {"title": "学习 Context API", "minutes": 60, "priority": 2},
                        {"title": "学习 Redux 状态管理", "minutes": 120, "priority": 1},
                        {"title": "学习性能优化技巧", "minutes": 60, "priority": 2},
                    ],
                },
            ],
        },
        "data analysis": {
            "name": "数据分析入门",
            "description": "掌握数据分析流程和常用工具",
            "goal_type": "knowledge",
            "estimated_hours": 40,
            "plans": [
                {
                    "name": "数据分析基础",
                    "description": "学习数据分析流程和统计学基础",
                    "tasks": [
                        {"title": "了解数据分析流程", "minutes": 60, "priority": 1},
                        {"title": "学习描述性统计", "minutes": 90, "priority": 1},
                        {"title": "学习数据可视化原理", "minutes": 60, "priority": 2},
                        {"title": "练习：使用 Excel 进行简单分析", "minutes": 60, "priority": 2},
                    ],
                },
                {
                    "name": "Python 数据分析",
                    "description": "学习使用 Python 进行数据分析",
                    "tasks": [
                        {"title": "学习 NumPy 基础", "minutes": 90, "priority": 1},
                        {"title": "学习 Pandas 数据处理", "minutes": 120, "priority": 1},
                        {"title": "学习数据清洗技巧", "minutes": 90, "priority": 1},
                        {"title": "实战：电商数据分析项目", "minutes": 180, "priority": 1},
                    ],
                },
            ],
        },
        "machine learning": {
            "name": "机器学习入门",
            "description": "掌握机器学习基础概念和常用算法",
            "goal_type": "skill",
            "estimated_hours": 100,
            "plans": [
                {
                    "name": "数学基础",
                    "description": "复习线性代数、微积分和概率论",
                    "tasks": [
                        {"title": "复习线性代数基础", "minutes": 120, "priority": 1},
                        {"title": "复习微积分基础", "minutes": 120, "priority": 1},
                        {"title": "复习概率论基础", "minutes": 90, "priority": 1},
                    ],
                },
                {
                    "name": "机器学习基础",
                    "description": "学习监督学习和无监督学习算法",
                    "tasks": [
                        {"title": "了解机器学习概述", "minutes": 60, "priority": 1},
                        {"title": "学习线性回归", "minutes": 120, "priority": 1},
                        {"title": "学习逻辑回归", "minutes": 90, "priority": 1},
                        {"title": "学习决策树和随机森林", "minutes": 120, "priority": 1},
                        {"title": "学习聚类算法", "minutes": 90, "priority": 2},
                    ],
                },
                {
                    "name": "实战项目",
                    "description": "动手实践机器学习项目",
                    "tasks": [
                        {"title": "学习 Scikit-learn 库", "minutes": 90, "priority": 1},
                        {"title": "实战：房价预测项目", "minutes": 180, "priority": 1},
                        {"title": "实战：图像分类项目", "minutes": 180, "priority": 1},
                    ],
                },
            ],
        },
    }

    matched_key = None
    for key in GOAL_TEMPLATES:
        if key in topic.lower():
            matched_key = key
            break

    if not matched_key:
        matched_key = "python"

    template = GOAL_TEMPLATES[matched_key]
    goal_id = _generate_id()

    goal: Dict[str, Any] = {
        "id": goal_id,
        "name": template["name"],
        "description": template["description"],
        "goal_type": template["goal_type"],
        "status": "active",
        "target_date": None,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "estimated_hours": template["estimated_hours"],
        "progress": 0.0,
        "tags": [topic],
        "prerequisites": [],
    }

    goals = _load_goals()
    goals[goal_id] = goal
    _save_goals(goals)

    plans = _load_plans()
    tasks = _load_tasks()

    for plan_template in template["plans"]:
        plan_id = _generate_id()
        plan: Dict[str, Any] = {
            "id": plan_id,
            "goal_id": goal_id,
            "name": plan_template["name"],
            "description": plan_template["description"],
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "total_tasks": len(plan_template["tasks"]),
            "completed_tasks": 0,
        }
        plans[plan_id] = plan

        for task_idx, task_template in enumerate(plan_template["tasks"]):
            task_id = _generate_id()
            scheduled_date = (today + timedelta(days=task_idx)).isoformat()
            task: Dict[str, Any] = {
                "id": task_id,
                "plan_id": plan_id,
                "goal_id": goal_id,
                "title": task_template["title"],
                "description": "",
                "status": "pending",
                "difficulty": "medium",
                "estimated_minutes": task_template["minutes"],
                "scheduled_date": scheduled_date,
                "completed_at": None,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "position": task_idx,
                "priority": task_template["priority"],
            }
            tasks[task_id] = task

    _save_plans(plans)
    _save_tasks(tasks)

    return {
        "message": f"学习计划「{template['name']}」已成功生成！",
        "goal_id": goal_id,
        "goal_name": template["name"],
        "total_plans": len(template["plans"]),
        "total_tasks": sum(len(p["tasks"]) for p in template["plans"]),
    }


def _load_wrong_questions() -> Dict[str, Dict[str, Any]]:
    path = _LEARNING_DATA_DIR / "wrong_questions.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save_wrong_questions(questions: Dict[str, Dict[str, Any]]) -> None:
    path = _LEARNING_DATA_DIR / "wrong_questions.json"
    path.write_text(json.dumps(questions, indent=2), encoding="utf-8")


def _load_question_reviews() -> Dict[str, Dict[str, Any]]:
    path = _LEARNING_DATA_DIR / "question_reviews.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save_question_reviews(reviews: Dict[str, Dict[str, Any]]) -> None:
    path = _LEARNING_DATA_DIR / "question_reviews.json"
    path.write_text(json.dumps(reviews, indent=2), encoding="utf-8")


def _load_similar_questions() -> Dict[str, Dict[str, Any]]:
    path = _LEARNING_DATA_DIR / "similar_questions.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save_similar_questions(questions: Dict[str, Dict[str, Any]]) -> None:
    path = _LEARNING_DATA_DIR / "similar_questions.json"
    path.write_text(json.dumps(questions, indent=2), encoding="utf-8")


class CreateWrongQuestionRequest(BaseModel):
    subject: str = Field(..., description="学科名称")
    knowledge_point: str = Field(..., description="知识点")
    question_content: str = Field(..., description="题目内容")
    user_answer: str = Field(..., description="用户答案")
    correct_answer: str = Field(..., description="正确答案")
    error_type: str = Field(..., description="错误类型")
    difficulty: str = Field(default="medium", description="难度")
    error_analysis: str = Field(default="", description="错误分析")
    solution: str = Field(default="", description="解题思路")
    tags: List[str] = Field(default_factory=list)


class UpdateWrongQuestionRequest(BaseModel):
    subject: Optional[str] = None
    knowledge_point: Optional[str] = None
    question_content: Optional[str] = None
    user_answer: Optional[str] = None
    correct_answer: Optional[str] = None
    error_type: Optional[str] = None
    difficulty: Optional[str] = None
    error_analysis: Optional[str] = None
    solution: Optional[str] = None
    tags: Optional[List[str]] = None


class ReviewQuestionRequest(BaseModel):
    user_answer: str = Field(..., description="用户答案")
    is_correct: bool = Field(..., description="是否正确")
    time_spent_seconds: int = Field(default=0)
    notes: str = Field(default="")


@router.get("/wrong-questions", response_model=List[Dict[str, Any]])
async def get_wrong_questions(
    request: Request,
    subject: Optional[str] = None,
    knowledge_point: Optional[str] = None,
    error_type: Optional[str] = None,
    difficulty: Optional[str] = None,
    mastery_status: Optional[str] = None,
):
    questions = _load_wrong_questions()
    result = list(questions.values())

    if subject:
        result = [q for q in result if q["subject"] == subject]
    if knowledge_point:
        result = [q for q in result if q["knowledge_point"] == knowledge_point]
    if error_type:
        result = [q for q in result if q["error_type"] == error_type]
    if difficulty:
        result = [q for q in result if q["difficulty"] == difficulty]
    if mastery_status:
        result = [q for q in result if q["mastery_status"] == mastery_status]

    result.sort(key=lambda q: q["created_at"], reverse=True)
    return result


@router.get("/wrong-questions/{question_id}", response_model=Dict[str, Any])
async def get_wrong_question(request: Request, question_id: str):
    questions = _load_wrong_questions()
    if question_id not in questions:
        raise HTTPException(status_code=404, detail="错题不存在")
    return questions[question_id]


@router.post("/wrong-questions", response_model=Dict[str, Any])
async def create_wrong_question(request: Request, body: CreateWrongQuestionRequest):
    questions = _load_wrong_questions()
    now = datetime.now(timezone.utc)
    question_id = _generate_id()

    try:
        ErrorType(body.error_type)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的错误类型")

    try:
        TaskDifficulty(body.difficulty)
    except ValueError:
        raise HTTPException(status_code=400, detail="无效的难度等级")

    question: Dict[str, Any] = {
        "id": question_id,
        "subject": body.subject,
        "knowledge_point": body.knowledge_point,
        "question_content": body.question_content,
        "user_answer": body.user_answer,
        "correct_answer": body.correct_answer,
        "error_type": body.error_type,
        "difficulty": body.difficulty,
        "mastery_status": "weak",
        "error_analysis": body.error_analysis,
        "solution": body.solution,
        "tags": body.tags,
        "image_path": None,
        "source_file": None,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
        "review_count": 0,
        "correct_count": 0,
        "last_review_at": None,
        "next_review_at": (now + timedelta(days=1)).isoformat(),
    }

    questions[question_id] = question
    _save_wrong_questions(questions)

    return question


@router.put("/wrong-questions/{question_id}", response_model=Dict[str, Any])
async def update_wrong_question(request: Request, question_id: str, body: UpdateWrongQuestionRequest):
    questions = _load_wrong_questions()
    if question_id not in questions:
        raise HTTPException(status_code=404, detail="错题不存在")

    question = questions[question_id]

    if body.subject is not None:
        question["subject"] = body.subject
    if body.knowledge_point is not None:
        question["knowledge_point"] = body.knowledge_point
    if body.question_content is not None:
        question["question_content"] = body.question_content
    if body.user_answer is not None:
        question["user_answer"] = body.user_answer
    if body.correct_answer is not None:
        question["correct_answer"] = body.correct_answer
    if body.error_type is not None:
        try:
            ErrorType(body.error_type)
            question["error_type"] = body.error_type
        except ValueError:
            raise HTTPException(status_code=400, detail="无效的错误类型")
    if body.difficulty is not None:
        try:
            TaskDifficulty(body.difficulty)
            question["difficulty"] = body.difficulty
        except ValueError:
            raise HTTPException(status_code=400, detail="无效的难度等级")
    if body.error_analysis is not None:
        question["error_analysis"] = body.error_analysis
    if body.solution is not None:
        question["solution"] = body.solution
    if body.tags is not None:
        question["tags"] = body.tags

    question["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_wrong_questions(questions)

    return question


@router.delete("/wrong-questions/{question_id}")
async def delete_wrong_question(request: Request, question_id: str):
    questions = _load_wrong_questions()
    if question_id not in questions:
        raise HTTPException(status_code=404, detail="错题不存在")

    del questions[question_id]
    _save_wrong_questions(questions)

    reviews = _load_question_reviews()
    remaining_reviews = {rid: r for rid, r in reviews.items() if r["question_id"] != question_id}
    _save_question_reviews(remaining_reviews)

    similar = _load_similar_questions()
    remaining_similar = {sid: s for sid, s in similar.items() if s["original_question_id"] != question_id}
    _save_similar_questions(remaining_similar)

    return {"message": "错题删除成功"}


@router.post("/wrong-questions/{question_id}/review", response_model=Dict[str, Any])
async def review_wrong_question(request: Request, question_id: str, body: ReviewQuestionRequest):
    questions = _load_wrong_questions()
    if question_id not in questions:
        raise HTTPException(status_code=404, detail="错题不存在")

    question = questions[question_id]
    now = datetime.now(timezone.utc)

    reviews = _load_question_reviews()
    review_id = _generate_id()
    review: Dict[str, Any] = {
        "id": review_id,
        "question_id": question_id,
        "user_answer": body.user_answer,
        "is_correct": body.is_correct,
        "review_date": now.isoformat(),
        "time_spent_seconds": body.time_spent_seconds,
        "notes": body.notes,
    }
    reviews[review_id] = review
    _save_question_reviews(reviews)

    question["review_count"] += 1
    question["last_review_at"] = now.isoformat()

    if body.is_correct:
        question["correct_count"] += 1
        correct_rate = question["correct_count"] / question["review_count"]
        if correct_rate >= 0.8:
            question["mastery_status"] = "mastered"
        elif correct_rate >= 0.6:
            question["mastery_status"] = "proficient"
        elif correct_rate >= 0.4:
            question["mastery_status"] = "basic"
        else:
            question["mastery_status"] = "weak"

        days_interval = min(int(question["review_count"] * 1.5), 30)
        question["next_review_at"] = (now + timedelta(days=days_interval)).isoformat()
    else:
        question["mastery_status"] = "weak"
        question["next_review_at"] = (now + timedelta(days=1)).isoformat()

    question["updated_at"] = now.isoformat()
    _save_wrong_questions(questions)

    return {
        "message": "复习记录已保存",
        "review_id": review_id,
        "mastery_status": question["mastery_status"],
        "next_review_at": question["next_review_at"],
    }


@router.get("/wrong-questions/{question_id}/reviews", response_model=List[Dict[str, Any]])
async def get_question_reviews(request: Request, question_id: str):
    reviews = _load_question_reviews()
    return [r for r in reviews.values() if r["question_id"] == question_id]


@router.get("/wrong-questions/stats", response_model=Dict[str, Any])
async def get_wrong_question_stats(request: Request):
    questions = _load_wrong_questions()

    total = len(questions)
    by_subject: Dict[str, int] = {}
    by_knowledge: Dict[str, int] = {}
    by_error_type: Dict[str, int] = {}
    by_mastery: Dict[str, int] = {}

    for q in questions.values():
        by_subject[q["subject"]] = by_subject.get(q["subject"], 0) + 1
        by_knowledge[q["knowledge_point"]] = by_knowledge.get(q["knowledge_point"], 0) + 1
        by_error_type[q["error_type"]] = by_error_type.get(q["error_type"], 0) + 1
        by_mastery[q["mastery_status"]] = by_mastery.get(q["mastery_status"], 0) + 1

    today = datetime.now(timezone.utc).date()
    need_review = sum(
        1 for q in questions.values()
        if q["next_review_at"] and _parse_datetime(q["next_review_at"]).date() <= today
    )

    return {
        "total": total,
        "by_subject": by_subject,
        "by_knowledge": by_knowledge,
        "by_error_type": by_error_type,
        "by_mastery": by_mastery,
        "need_review": need_review,
    }


@router.get("/wrong-questions/need-review", response_model=List[Dict[str, Any]])
async def get_questions_need_review(request: Request):
    questions = _load_wrong_questions()
    today = datetime.now(timezone.utc).date()

    result = [
        q for q in questions.values()
        if q["next_review_at"] and _parse_datetime(q["next_review_at"]).date() <= today
    ]

    result.sort(key=lambda q: q["next_review_at"])
    return result