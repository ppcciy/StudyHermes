---
title: "Learning Goals — Create and manage learning objectives"
description: "Create, track, and manage your learning goals"
version: "1.0.0"
author: "HermesLearn"
license: "MIT"
platforms:
  - linux
  - macos
  - windows
tags:
  - learning
  - goals
  - planning
  - productivity
---

# Learning Goals Manager

Manage your learning goals effectively. Create, track, and update your objectives to stay on the path to success.

## Commands

### Create a new learning goal

```
/learning-goals create <goal_name> [--type skill|knowledge|certification|project] [--hours <hours>] [--target-date <date>]
```

### List all goals

```
/learning-goals list [--active|--completed|--paused]
```

### Update goal progress

```
/learning-goals progress <goal_id> <percentage>
```

### Update goal status

```
/learning-goals status <goal_id> <active|completed|paused|abandoned>
```

### Delete a goal

```
/learning-goals delete <goal_id>
```

## Goal Types

- **skill**: Acquire a new practical skill (e.g., Python programming, graphic design)
- **knowledge**: Learn theoretical knowledge (e.g., machine learning concepts, history)
- **certification**: Prepare for a specific certification (e.g., AWS, PMP)
- **project**: Complete a project to demonstrate mastery (e.g., build a website)

## Best Practices

1. Set clear, specific goals
2. Break large goals into smaller milestones
3. Track progress regularly
4. Adjust goals as needed
5. Celebrate achievements

## Examples

```
/learning-goals create "Learn Python programming" --type skill --hours 100 --target-date 2025-12-31
/learning-goals list --active
/learning-goals progress abc123 35
/learning-goals status abc123 completed
```