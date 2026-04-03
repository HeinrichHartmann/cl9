# cl9 Tasks

This folder contains task specifications for the implementation model.

## Workflow

1. **Design model** creates task files (`NNN-description.md`)
2. **Implementation model** picks up tasks, implements them
3. **Implementation model** updates the task file when done (Status → Done)
4. **Implementation model** updates relevant documentation (README, ADRs)

## Task File Format

```markdown
# Task NNN: Title

**Status**: Open | In Progress | Done | Blocked
**Priority**: High | Medium | Low
**Type**: Bug | Enhancement | Feature | Refactor

## Problem
[Description of the issue or need]

## Required Changes
[Specific changes needed]

## Files to Modify
[List of files]

## Completion Criteria
[Checklist]

---
**When done**: [Instructions for completion]
```

## Current Tasks

| ID | Title | Status |
|----|-------|--------|
| 001 | Revise CLI for init/enter | Done |
| 002 | Environment Types | Done |
| 003 | Env Update Command | Done |
