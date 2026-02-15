# Firedantic2 Rebuild - Implementation Plan

## Goal

Rebuild firedantic from scratch as **firedantic2** with a modern, type-safe, Firestore-native interface while maintaining the ability for existing users to migrate from the current firedantic implementation.

### Key Objectives

- **Firestore-native Interface**: Align with Google Firestore's actual client API patterns rather than MongoDB-like abstractions
- **Full Type Safety**: Complete type hints throughout, especially for subcollections (a known pain point)
- **Pydantic 2**: Based on latest Pydantic 2.x (currently 2.12.4+)
- **Module Standards**: Follow Everygene module standards for organization and testability
- **Migration Friendly**: Provide adapters for existing firedantic users to ease migration
- **Collaborative Development**: Structure for easy work-splitting between team members

## Background Context

### Current State (0.13.1)

- Uses `unasync` to generate sync code from async source
- MongoDB-like query interface (`find()`, `find_one()`, etc.)
- Pydantic 2 models but incomplete type hints
- New multi-configuration pattern for managing multiple Firestore projects/databases
- Subcollections support but weak typing

### Pain Points to Address

1. **Type Hints**: Especially weak for subcollections
2. **Interface Mismatch**: MongoDB-like API doesn't match Firestore's native patterns
3. **Code Generation**: `unasync` script approach works but may have modern alternatives

## Architecture Decisions

### Async/Sync Strategy ✅

**Decision**: Continue with `unasync` but improve it with better type preservation

**Rationale**:

- Proven approach that works well in practice
- Avoids dual maintenance burden
- Still viable in 2026 (alternatives like `asyncio.to_thread()` add runtime overhead)
- Can enhance to better preserve type hints, especially generics and `Self`

### Interface Design ✅

**Decision**: Firestore-native primary interface with MongoDB-style adapter for compatibility

**Architecture**:

- **Core**: `.document()`, `.collection()`, `.get()`, `.set()`, `.stream()` (Firestore-native)
- **Adapter**: `.find()`, `.find_one()`, `.upsert()` (MongoDB-style, backwards compatible)

**Rationale**:

- More intuitive for users familiar with Firestore SDK
- Better type hints (native SDK patterns designed for static typing)
- Allows existing firedantic users to migrate gradually via adapter
- Clear migration path from adapter → native

---

## Proposed Changes

### Core Architecture

#### [NEW] `firedantic/_async/client.py`

Firestore-native client wrapper providing type-safe access to collections and documents:

- `FirestoreClient` class wrapping `google.cloud.firestore.AsyncClient`
- Type-safe `.collection()` returning typed collection references
- Configuration management via config system

#### [NEW] `firedantic/_async/collection.py`

Type-safe collection reference wrapper:

- `Collection[T]` generic class for type-safe collection operations
- Methods: `.document()`, `.stream()`, `.add()`, `.list_documents()`
- Proper type hints for all return values
- Query builder with full type safety

#### [NEW] `firedantic/_async/document.py`

Type-safe document reference and operations:

- `Document[T]` generic class
- Methods: `.get()`, `.set()`, `.update()`, `.delete()`
- Transaction support with proper typing
- Subcollection access with preserved types

#### [NEW] `firedantic/_async/query.py`

Firestore-native query builder:

- Chainable query methods: `.where()`, `.order_by()`, `.limit()`, `.offset()`
- Type-safe filters using `FieldFilter`
- Returns typed iterators

#### [NEW] `firedantic/_async/transaction.py`

Transaction wrapper with type safety:

- Context manager for transactions
- Batch operations
- Proper error handling

---

### Model Layer

#### [MODIFY] `firedantic/_async/model.py`

Refactor to use new core primitives while maintaining backwards compatibility:

- Keep `AsyncModel` base class for familiar entry point
- Internally delegate to new collection/document classes
- Add `.ref` property returning typed `Document[Self]`
- Add `.collection_ref` classmethod returning typed `Collection[Self]`
- Improved subcollection typing

#### [NEW] `firedantic/_async/subcollection.py`

Dedicated subcollection module with full type hints:

- `SubCollection[T]` generic with parent relationship tracking
- Proper generics for `.model_for()` pattern
- Type-safe navigation between parent and subcollections

---

### Compatibility Layer (Adapters)

#### [NEW] `firedantic/adapters/mongodb_style.py`

MongoDB-like interface adapter for migration:

- `MongoStyleModel` mixin providing `.find()`, `.find_one()`, `.upsert()`
- Maps to underlying Firestore-native operations
- Deprecation warnings for eventual removal
- Full backward compatibility with current firedantic

---

### Configuration & Utilities

#### [MODIFY] `firedantic/configurations.py`

Enhanced configuration system:

- Better typing for configuration objects
- Lazy client initialization improvements
- Support for multiple projects/databases (already exists, improve types)

#### [MODIFY] `firedantic/exceptions.py`

Add new exceptions as needed:

- `TransactionError`
- `QueryValidationError`

Keep existing ones for compatibility

---

### Testing Infrastructure

#### Testing Strategy: Unit + Integration

**Philosophy**: ~70% unit tests (fast, no emulator), ~30% integration tests (with emulator)

**Unit Tests (No Emulator)**:

- Model validation, query building, config management
- Type transformations, exception handling, adapter logic
- Fast feedback loop during development

**Integration Tests (With Emulator)**:

- CRUD operations, query execution, transactions
- Subcollections, index creation, multi-config
- Validates actual Firestore SDK behavior

#### [NEW] `firedantic/tests/test_firestore_native.py`

Tests for new Firestore-native interface:

- Collection/document operations
- Query building
- Transaction handling
- Type checking validation

#### [NEW] `firedantic/tests/test_adapters.py`

Tests for compatibility adapters:

- MongoDB-style operations
- Migration scenarios
- Backward compatibility validation

#### [MODIFY] `firedantic/tests/tests_async/test_model.py`

Update existing tests to work with refactored model

---

### Documentation

#### [NEW] `docs/ARCHITECTURE.md`

Architecture overview:

- Design philosophy
- Core concepts
- Type system explanation
- Firestore-native vs adapter patterns

#### [NEW] `docs/MIGRATION.md`

Migration guide from firedantic 0.x to 2.0:

- Breaking changes
- Adapter usage
- Step-by-step migration path
- Code examples

#### [NEW] `docs/COLLABORATION.md`

Guide for collaborative development:

- Work split recommendations
- Development workflow
- Testing strategy
- Code review process

#### [MODIFY] `README.md`

Major update with:

- New vision for firedantic2
- Quick start with Firestore-native interface
- Migration notes
- Full type hints examples
- Subcollection examples with proper types

---

### Build & Tooling

#### [MODIFY] `unasync.py`

Improvements for better type hint preservation:

- Handle `Self` type properly
- Preserve generics in sync version
- Better handling of type variable transformations

#### [MODIFY] `pyproject.toml`

- Bump version to 2.0.0-alpha.1
- Update dependencies to latest compatible versions
- Add pytest markers for unit/integration tests
- Consider `pyright` for stricter type checking

---

## Verification Plan

### Test Execution Strategy

**Philosophy**: ~70% unit tests (fast, no emulator), ~30% integration tests (with emulator)

#### Unit Tests (No Emulator Required)

**What to unit test:**

- Model validation (Pydantic validation logic, field constraints)
- Query building (validate query objects are constructed correctly, don't execute)
- Configuration management (config resolution, prefix handling)
- Type transformations (data serialization, field mapping)
- Exception handling (error cases, validation errors)
- Adapter logic (MongoDB-style adapter translation to Firestore-native)

**Benefits:**

- ⚡ Fast (milliseconds vs seconds)
- 🚀 Run in CI without emulator setup
- 🎯 Isolate business logic bugs
- 🔍 Easy to debug specific edge cases

**Example:**

```python
import pytest
from firedantic2 import QueryBuilder

pytestmark = [pytest.mark.unit]

def test_query_builder_constructs_where_clause():
    """Unit test - validates query construction without executing."""
    mock_collection = Mock()
    builder = QueryBuilder(mock_collection)
    query = builder.where("name", "==", "Acme")

    assert query._filters[0].field == "name"
    assert query._filters[0].value == "Acme"
    # No actual Firestore call made
```

#### Integration Tests (With Emulator)

**What to integration test:**

- Actual CRUD operations (save, get, update, delete with real Firestore)
- Query execution (filters, ordering, pagination actually work)
- Transactions (transaction semantics work correctly)
- Subcollections (parent-child relationships)
- Index creation (composite indexes actually get created)
- Multi-config (multiple projects/databases work)

**Benefits:**

- 🔒 Validates actual Firestore SDK behavior
- 🐛 Catches serialization bugs
- ✅ Ensures Google's SDK changes don't break you
- 🔄 Tests transaction semantics accurately

**Example:**

```python
import pytest

pytestmark = [pytest.mark.integration]

@pytest.mark.asyncio
async def test_save_and_retrieve_company():
    """Integration test - requires Firestore emulator."""
    company = Company(name="Acme Corp", revenue=100000)
    await company.save()

    retrieved = await Company.get_by_id(company.id)
    assert retrieved.name == "Acme Corp"
    assert retrieved.revenue == 100000

    await company.delete()
```

#### Pytest Configuration

Update `pyproject.toml`:

```toml
[tool.pytest.ini_options]
markers = [
    "unit: fast unit tests (no emulator) - DEFAULT",
    "integration: tests requiring emulator - OPT-IN",
]
```

### Automated Test Commands

```bash
# Unit tests only (fast, no emulator needed)
poetry run pytest -m unit -v

# Integration tests (requires emulator)
./start_emulator.sh  # In separate terminal
poetry run pytest -m integration -v

# All tests
./start_emulator.sh  # In separate terminal
poetry run pytest -v

# Run sync tests (generated via unasync)
poetry run invoke unasync
poetry run pytest firedantic/tests/tests_sync/ -v

# Type checking with mypy
poetry run mypy firedantic/_async/

# Full verification suite (what CI will run)
./start_emulator.sh
poetry run pytest -v
poetry run mypy firedantic/_async/
poetry run ruff check firedantic/
```

### Manual Verification

1. **Type Hints Validation**:
   - Open code in VS Code / PyCharm
   - Verify autocomplete works for subcollections
   - Check that generic types are preserved
   - Validate no `Any` types in public APIs

2. **Migration Path Testing**:
   - Create sample project using old firedantic style
   - Apply MongoDB adapter
   - Verify deprecation warnings appear
   - Migrate to Firestore-native style
   - Confirm both work identically

3. **Documentation Review**:
   - Read through README with fresh eyes
   - Follow migration guide step-by-step
   - Verify all code examples are runnable

---

## Work Split Recommendation

### Phase 1: Foundation (Can Work in Parallel)

**Core Infrastructure**:

- Core infrastructure (`client.py`, `collection.py`, `document.py`)
- Configuration enhancements
- Build tooling (`unasync.py` improvements)

**Model & Query Layer**:

- Model layer refactoring (`model.py`)
- Subcollection typing (`subcollection.py`)
- Query builder (`query.py`)

### Phase 2: Integration (Collaborative)

**Together**:

- Wire up model layer to core infrastructure
- Test integration points
- Debug type issues

### Phase 3: Compatibility & Polish

**Compatibility Layer**:

- MongoDB-style adapter
- Migration documentation
- README updates

**Testing & Docs**:

- Transaction wrapper
- Test suite expansion
- Architecture documentation

---

## Migration Path for Users

### Stage 1: Install firedantic2 alongside firedantic

```python
# Install both
pip install firedantic firedantic2
```

### Stage 2: Use MongoDB adapter (no code changes needed)

```python
from firedantic2.adapters.mongodb_style import Model

# Existing code works as-is
class Company(Model):
    __collection__ = "companies"
    name: str

results = Company.find({"name": "Acme"})  # Still works!
```

### Stage 3: Migrate to Firestore-native (gradual)

```python
from firedantic2 import Model, Collection

class Company(Model):
    __collection__ = "companies"
    name: str

# New style
companies: Collection[Company] = Company.collection_ref()
async for company in companies.stream():
    print(company.name)

# Or query-style
results = companies.where("name", "==", "Acme").stream()
```

---

## Timeline & Milestones

### Milestone 1: Skeleton & Documentation ✅

- ✅ Implementation plan
- ✅ README with vision
- ✅ Architecture docs
- ✅ Collaboration guide

### Milestone 2: Core Infrastructure

- [ ] Firestore-native client/collection/document
- [ ] Basic type system working
- [ ] Unasync improvements

### Milestone 3: Model Layer

- [ ] Refactored model
- [ ] Subcollection typing
- [ ] Query builder

### Milestone 4: Compatibility

- [ ] MongoDB adapter
- [ ] Migration guide
- [ ] Full test coverage

### Milestone 5: Release

- [ ] Documentation polish
- [ ] Performance testing
- [ ] 2.0.0 release
