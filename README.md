# Firedantic2 🔥

> **Modern, type-safe Pydantic models for Google Firestore**

Firedantic2 is a from-scratch rebuild of firedantic with a Firestore-native interface, complete type hints, and Pydantic 2 support.

**Status**: 🚧 In Development (firedantic2 branch)

---

## ✨ What's New in 2.0

- **🎯 Firestore-Native Interface**: Matches Google's Firestore SDK patterns (`.collection()`, `.document()`, `.get()`, `.set()`)
- **🔒 Full Type Safety**: Complete type hints throughout, including proper subcollection typing
- **⚡ Pydantic 2**: Built on the latest Pydantic 2.x with all performance and validation improvements
- **🔄 Migration Friendly**: MongoDB-style adapter for easy migration from firedantic 0.x
- **🏗️ Clean Architecture**: Modern Python best practices for testability and maintainability

---

## 🚀 Quick Start (Preview)

### Firestore-Native Style (Recommended)

```python
from firedantic2 import Model, Collection
from pydantic import BaseModel

class Owner(BaseModel):
    first_name: str
    last_name: str

class Company(Model):
    __collection__ = "companies"
    name: str
    owner: Owner
    revenue: int

# Type-safe collection access
companies: Collection[Company] = Company.collection_ref()

# Create
acme = Company(name="Acme Corp", owner=Owner(first_name="John", last_name="Doe"), revenue=1000000)
await acme.save()

# Query with full type inference
async for company in companies.where("revenue", ">=", 500000).stream():
    print(f"{company.name}: ${company.revenue}")  # ← Full autocomplete!

# Get by ID
company = await Company.get_by_id("acme-corp")

# Update
company.revenue = 2000000
await company.save()

# Delete
await company.delete()
```

### MongoDB-Style (For Migration)

```python
from firedantic2.adapters.mongodb_style import Model

class Company(Model):
    __collection__ = "companies"
    name: str

# Your existing code works as-is!
companies = Company.find({"name": "Acme Corp"})
company = Company.find_one({"name": "Acme Corp"})
```

---

## 🌟 Key Features (Planned)

### Complete Type Hints

```python
from typing import Optional
from datetime import datetime
from firedantic2 import Model, SubCollection, SubModel

class UserStats(SubModel):
    purchases: int = 0
    last_login: Optional[datetime] = None

    class Collection(SubCollection):
        __collection_tpl__ = "users/{id}/stats"

class User(Model):
    __collection__ = "users"
    name: str

# ✨ Full type inference for subcollections!
user = await User.get_by_id("user123")
stats_collection = UserStats.collection_for(user)  # ← Typed correctly!
stats = await stats_collection.document("2024").get()  # ← Returns UserStats
```

### Multiple Configurations

```python
from firedantic2.configurations import configuration

# Default config
configuration.add(prefix="app-", project="my-project")

# Additional config for different project
configuration.add(
    name="analytics",
    prefix="analytics-",
    project="analytics-project"
)

class Event(Model):
    __collection__ = "events"
    __db_config__ = "analytics"  # ← Use non-default config
    event_type: str
    timestamp: datetime
```

### Transactions

```python
from firedantic2 import transaction

async with transaction() as tx:
    company = await Company.get_by_id("acme-corp", transaction=tx)
    company.revenue += 100000
    await company.save(transaction=tx)
```

---

## 📚 Documentation

- [Implementation Plan](docs/IMPLEMENTATION_PLAN.md) - Full architecture and roadmap
- [Collaboration Guide](docs/COLLABORATION.md) - Development workflow
- [Architecture Overview](docs/ARCHITECTURE.md) - Coming soon
- [Migration Guide](docs/MIGRATION.md) - Coming soon

---

## 🔄 Migration from Firedantic 0.x

Firedantic2 is designed to make migration easy:

1. **Install alongside**: `pip install firedantic2` (can coexist with firedantic)
2. **Use adapter**: Import from `firedantic2.adapters.mongodb_style` - no code changes needed
3. **Migrate gradually**: Move to Firestore-native interface module-by-module
4. **Remove adapter**: Once migration is complete, use native interface everywhere

See [MIGRATION.md](docs/MIGRATION.md) for detailed guide (coming soon).

---

## ⚙️ Development

### Prerequisites

- Python 3.10+
- Poetry
- Firebase Emulator Suite
- Node.js 22+

### Setup

```bash
# Clone repo
git clone https://github.com/altissimo-hq/firedantic
cd firedantic
git checkout firedantic2

# Install dependencies
poetry install

# Start Firestore emulator
./start_emulator.sh

# Run tests
poetry run pytest -v

# Generate sync version from async
poetry run invoke unasync
```

### Running Tests

```bash
# Unit tests (fast, no emulator)
poetry run pytest -m unit -v

# Integration tests (requires emulator)
./start_emulator.sh  # In separate terminal
poetry run pytest -m integration -v

# All tests
poetry run pytest -v

# Type checking
poetry run mypy firedantic/_async/
```

---

## 🤝 Contributing

We welcome contributions! See [COLLABORATION.md](docs/COLLABORATION.md) for development workflow and guidelines.

---

## 📜 License

BSD 3-Clause License - see [LICENSE](LICENSE)

---

## 🙏 Credits

- Original [firedantic](https://github.com/ioxiocom/firedantic) by IOXIO Ltd
- Multi-configuration support by Marissa Fisher (@mfisher29)
- Firedantic2 rebuild by Altissimo HQ

---

## Roadmap

**Current Phase**: Foundation & Planning ✅

### Milestones

- [x] Implementation plan
- [x] Architecture documentation
- [x] Collaboration guide
- [ ] Core infrastructure (client, collection, document)
- [ ] Model layer refactoring
- [ ] Query builder
- [ ] MongoDB-style adapter
- [ ] Full test coverage
- [ ] Migration guide
- [ ] 2.0.0 release

---

**For current stable version (0.13.1)**, check out the `main` branch.
