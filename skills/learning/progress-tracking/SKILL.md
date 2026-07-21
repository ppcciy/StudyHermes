---
title: "Progress Tracking — Monitor your learning journey"
description: "Track learning progress, analyze patterns, and get insights"
version: "1.0.0"
author: "HermesLearn"
license: "MIT"
platforms:
  - linux
  - macos
  - windows
tags:
  - learning
  - tracking
  - analytics
  - productivity
---

# Learning Progress Tracker

Track your learning progress, analyze patterns, and get actionable insights to optimize your learning journey.

## Commands

### View progress overview

```
/progress [--goal <goal_id>] [--period week|month|year]
```

### Log study session

```
/progress log <goal_id> <minutes> [--notes <notes>]
```

### Get learning analytics

```
/progress analytics [--goal <goal_id>]
```

### Set daily target

```
/progress target <minutes_per_day>
```

## Features

### Progress Dashboard
- Visual progress bars for each goal
- Time spent breakdown
- Completion percentage
- Streak tracking

### Analytics
- Learning speed analysis
- Best learning times
- Topic difficulty assessment
- Knowledge retention rate
- Weekly/monthly trends

### Insights
- Personalized recommendations
- Warning flags for slow progress
- Achievement milestones
- Improvement suggestions

### Reports
- Weekly progress summary
- Monthly learning report
- Goal completion celebration
- Comparative analysis

## Data Tracking

### What gets tracked
- Study session duration
- Topics covered
- Goals worked on
- Learning outcomes
- Notes and reflections

### Privacy
- All data stored locally
- No external data sharing
- Full control over your data

## Best Practices

1. **Log regularly**: Track sessions daily
2. **Be honest**: Record actual time spent
3. **Add notes**: Document what you learned
4. **Review weekly**: Check progress trends
5. **Adjust as needed**: Modify goals based on insights

## Example Output

```
📊 Learning Progress Report

Active Goals:
├── Learn Python: ████████████░░░░░░░░ 55% (11/20 hours)
├── Machine Learning: ██████░░░░░░░░░░ 30% (6/20 hours)
└── Data Visualization: ██░░░░░░░░░░░░ 10% (2/20 hours)

This Week:
- Total study time: 12 hours 30 minutes
- Average daily: 1 hour 47 minutes
- Best day: Wednesday (3 hours)

📈 Insights:
- Your Python progress is on track!
- Consider spending more time on ML fundamentals
- Your best learning time is between 9-11 AM
```

## Streak System

- Daily streak counter
- Streak bonuses and rewards
- Motivation milestones
- Streak recovery suggestions