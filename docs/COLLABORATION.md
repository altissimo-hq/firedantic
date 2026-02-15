# Firedantic2 Collaboration Guide

This document outlines how to work collaboratively on rebuilding firedantic as firedantic2.

## Development Workflow

### Branch Strategy

```text
main (0.13.1 - current stable)
  ↓
firedantic2 (2.0 rebuild - working branch)
  ↓
feature-branch-name
```

**Workflow**:

1. Create feature branches from `firedantic2`
2. Work on assigned area
3. Create PR back to `firedantic2` when ready
4. Review each other's code
5. Merge when approved

### Phase-Based Development

Work in phases to minimize conflicts and maintain momentum:

#### Phase 1: Foundation (Parallel Work)

**Core Infrastructure**:

- [ ] `firedantic/_async/client.py` - Firestore client wrapper
- [ ] `firedantic/_async/collection.py` - Collection reference wrapper
- [ ] `firedantic/_async/document.py` - Document reference wrapper
- [ ] `firedantic/configurations.py` - Enhanced configuration
- [ ] `unasync.py` - Improvements for type preservation

**Model & Query Layer**:

- [ ] `firedantic/_async/model.py` - Refactored model layer
- [ ] `firedantic/_async/subcollection.py` - Subcollection with full typing
- [ ] `firedantic/_async/query.py` - Query builder
- [ ] `firedantic/tests/test_model.py` - Model tests

**Sync Point**: When both areas complete, schedule call to integrate

---

#### Phase 2: Integration (Collaborative)

**Together** (pair programming session or video call):

- [ ] Wire model layer to core infrastructure
- [ ] Resolve type conflicts at integration points
- [ ] Get basic end-to-end flow working
- [ ] Write integration tests

**Goal**: Have a working prototype with basic functionality

---

#### Phase 3: Compatibility & Polish (Parallel Work)

**Compatibility Layer**:

- [ ] `firedantic/adapters/mongodb_style.py` - Compatibility adapter
- [ ] `docs/MIGRATION.md` - Migration guide
- [ ] `README.md` - Final README updates
- [ ] Migration test suite

**Testing & Docs**:

- [ ] `firedantic/_async/transaction.py` - Transaction wrapper
- [ ] `docs/ARCHITECTURE.md` - Architecture docs
- [ ] Test suite expansion
- [ ] Type checking validation

---

## Communication

### Daily Standups (Async)

Post updates regularly:

```text
Yesterday: <what you completed>
Today: <what you're working on>
Blockers: <anything blocking you>
```

### Sync Meetings

- **Weekly sync**: 30 min video call every week
- **Integration sessions**: Schedule ad-hoc when needed (Phase 2)
- **Code reviews**: Async via GitHub PRs

### Questions & Decisions

- **Quick questions**: Slack/Discord/chat
- **Design decisions**: GitHub Discussions or issues
- **Urgent blockers**: Video call

---

## Code Review Guidelines

### What to Look For

**Functionality**:

- [ ] Does it solve the problem?
- [ ] Are edge cases handled?
- [ ] Are errors handled appropriately?

**Type Safety**:

- [ ] Are all public methods fully typed?
- [ ] Do generics work correctly?
- [ ] No `Any` types in public APIs?

**Testing**:

- [ ] Are there tests for new code?
- [ ] Do tests cover edge cases?
- [ ] Are unit tests separate from integration tests?

**Documentation**:

- [ ] Are docstrings present?
- [ ] Are complex algorithms explained?
- [ ] Is the README updated if needed?

### Review Process

1. **Author** creates PR with:
   - Clear title
   - Description of changes
   - Testing done
   - Screenshots/examples if applicable

2. **Reviewer** reviews within 24 hours:
   - Leave comments/questions
   - Request changes if needed
   - Approve when ready

3. **Author** addresses feedback:
   - Respond to all comments
   - Make requested changes
   - Re-request review

4. **Merge**:
   - Author merges own PR after approval
   - Delete feature branch after merge

---

## Development Setup

### First Time Setup

```bash
# Clone and switch to firedantic2 branch
cd ~/src/altissimo/firedantic
git checkout firedantic2
git pull origin firedantic2

# Install dependencies
poetry install

# Install pre-commit hooks
poetry run pre-commit install

# Start emulator (in separate terminal)
./start_emulator.sh
```

### Daily Development

```bash
# Pull latest from firedantic2
git checkout firedantic2
git pull origin firedantic2

# Create your feature branch
git checkout -b feature-name

# Make changes...

# Run tests frequently
poetry run pytest -m unit -v

# Generate sync code
poetry run invoke unasync

# Type check
poetry run mypy firedantic/_async/

# When done, push and create PR
git push origin feature-name
```

---

## Testing Strategy

### Unit Tests (Fast, No Network)

- Mock Firestore client
- Test business logic
- Test type system
- Run on every commit

```bash
poetry run pytest -m unit -v
```

### Integration Tests (With Emulator)

- Real Firestore client (emulator)
- End-to-end flows
- Run before merging

```bash
./start_emulator.sh  # In separate terminal
poetry run pytest -m integration -v
```

### Type Tests

- Validate type hints work correctly
- Use mypy
- Run before merging

```bash
poetry run mypy firedantic/_async/
```

---

## Conflict Resolution

If both modifying the same file:

1. **Communicate early**: Post if you're about to modify a shared file
2. **Rebase often**: Pull from `firedantic2` frequently
3. **Ask for help**: If merge conflict is complex, schedule quick call
4. **Be generous**: If in doubt, defer to the person who "owns" that module

---

## Code Style & Conventions

### General

- Use `ruff` for linting (configured in `pyproject.toml`)
- Type hints on all public methods
- Docstrings for public classes/methods

### Naming

- Classes: `PascalCase`
- Functions/methods: `snake_case`
- Private: `_leading_underscore`
- Type variables: `T`, `TModel`, etc.

### Imports

```python
from __future__ import annotations

from typing import TYPE_CHECKING

# Standard library
import asyncio
from dataclasses import dataclass

# Third-party
from pydantic import BaseModel
from google.cloud.firestore_v1 import AsyncClient

# Local
from firedantic.exceptions import ModelNotFoundError

if TYPE_CHECKING:
    from firedantic.model import Model
```

---

## Milestone Checklist

### M1: Skeleton & Docs ✅

- [x] Implementation plan
- [x] README draft
- [x] Architecture docs
- [x] Collaboration guide

### M2: Core Infrastructure

- [ ] Client wrapper
- [ ] Collection wrapper
- [ ] Document wrapper
- [ ] Basic tests

### M3: Model Layer

- [ ] Refactored model
- [ ] Subcollection typing
- [ ] Query builder
- [ ] Model tests

### M4: Integration

- [ ] Wire everything together
- [ ] End-to-end tests
- [ ] Type validation passes

### M5: Compatibility

- [ ] MongoDB adapter
- [ ] Migration guide
- [ ] Backward compat tests

### M6: Release

- [ ] Documentation complete
- [ ] All tests passing
- [ ] Performance validated
- [ ] 2.0.0 release!

---

## Tips for Success

1. **Communicate often**: Over-communication is better than under-communication
2. **Small PRs**: Keep PRs focused and small for easier review
3. **Test early**: Write tests as you go, not at the end
4. **Ask questions**: No question is too small
5. **Celebrate wins**: Share progress and celebrate milestones!
6. **Be patient**: This is a big rebuild, take it one step at a time
7. **Have fun**: We're building something cool together! 🎉
