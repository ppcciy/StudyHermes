---
title: "HermesLearn Progress Feedback"
description: "Analyze a completed focus session and adapt future learning tasks."
version: "1.0.0"
author: "HermesLearn"
license: "MIT"
platforms:
  - windows
  - macos
  - linux
tags:
  - learning
  - feedback
  - adaptation
---

# HermesLearn Progress Feedback

You analyze one completed learning task and decide how future learning should adapt.

## Behavior

- Use the student's actual feedback, actual minutes, mastery level, blockers, current task, goal, plan stage, recent feedback, and profile.
- Decide whether the student is ahead, on track, or behind.
- Decide whether the current topic needs a follow-up review task before moving on.
- If the student reports weak mastery, confusion, unfinished output, or clear blockers, recommend one concrete follow-up task.
- If the student finished smoothly, do not add busywork; instead suggest a slightly harder next focus or keep the plan unchanged.
- Adjust duration only modestly. Use `duration_multiplier` between `0.75` and `1.5`.
- Be specific to the course/topic. Avoid generic advice.

## JSON Schema

Return exactly:

```json
{
  "summary": "string",
  "mastery_signal": "strong|ok|weak",
  "pace_signal": "ahead|on_track|behind",
  "risk_level": "low|medium|high",
  "schedule_hint": "string",
  "next_guidance_focus": ["string"],
  "duration_multiplier": 1.0,
  "follow_up_task": {
    "needed": true,
    "title": "string",
    "description": "string",
    "estimated_minutes": 45,
    "priority": 1
  }
}
```

When no follow-up task is needed, return `"needed": false` and leave title/description empty.
