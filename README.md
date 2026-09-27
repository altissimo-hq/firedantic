# Firedantic

[![GitHub Workflow Status](https://img.shields.io/github/actions/workflow/status/ioxiocom/firedantic/publish.yaml)](https://github.com/ioxiocom/firedantic/actions/workflows/publish.yaml)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![PyPI](https://img.shields.io/pypi/v/firedantic)](https://pypi.org/project/firedantic/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/firedantic)](https://pypi.org/project/firedantic/)
[![License: BSD 3-Clause](https://img.shields.io/badge/License-BSD%203--Clause-blue.svg)](https://opensource.org/licenses/BSD-3-Clause)

> **About this fork:** This is the actively maintained fork of firedantic under
> altissimo-hq. The original upstream repository
> ([ioxiocom/firedantic](https://github.com/ioxiocom/firedantic)) has no active
> maintainer as of January 2026. This fork includes Marissa Fisher's (@mfisher29)
> comprehensive multi-configuration support (v0.13.0) and will continue to receive
> updates, bug fixes, and new features.

Database models for Firestore using Pydantic base models.

## Installation

The package is available on PyPI:

```bash
pip install firedantic
```

## Quick overview

Firedantic provides simple Pydantic-based models for Firestore, with both sync and async
model classes, helpers for composite indexes and TTL policies, and a new configuration
system that supports multiple named Firestore connections.

## Usage

### Migration Guide: Legacy `configure()` → New `Configuration`

We introduced a new, more flexible `Configuration` registry to support multiple
Firestore clients, lazy client creation, and admin clients. The legacy `configure()`
helper is still supported for backwards compatibility, but it is now considered
**deprecated**. Scroll below for legacy instructions.

### New (Recommended) Usage

```python
from firedantic.configurations import configuration

# default config
configuration.add(prefix="app-", project="my-project")

# extra config
configuration.add(name="billing", prefix="billing-", project="billing-project")

# get clients
client = configuration.get_client()                  # sync client for "(default)"
async_client = configuration.get_async_client()      # async client for "(default)"
billing_client = configuration.get_client("billing") # sync client for "billing"
```

Notes:

- `configuration.add(...)` accepts either client/async_client (pre-built) or
  project+credentials and will lazily create clients.
- Models can declare `__db_config__ = "custom-name"` to use a named configuration or
  omit it to use the `"(default)"` config.
- Backwards-compatible helpers (`configure()`, `CONFIGURATIONS`) will still populate the
  old surface but are deprecated.

### Old (Legacy – Still Works, Deprecated) Usage

In your application you will need to configure the firestore db client and optionally
the collection prefix, which by default is empty.

```python
from os import environ
from unittest.mock import Mock

import google.auth.credentials
from firedantic import configure
from google.cloud.firestore import Client

# Firestore emulator must be running if using locally.
if environ.get("FIRESTORE_EMULATOR_HOST"):
    client = Client(
        project="firedantic-test",
        credentials=Mock(spec=google.auth.credentials.Credentials)
    )
else:
    client = Client()

configure(client, prefix="firedantic-test-")
```

You may also still use:

```python
from firedantic.configurations import CONFIGURATIONS

db = CONFIGURATIONS["db"]
prefix = CONFIGURATIONS["prefix"]
```

### Defining Models

Once that is done, you can start defining your Pydantic models. Models are Pydantic
classes that extend Firedantic’s sync Model or async AsyncModel:

#### Sync Model Example

```python
from pydantic import BaseModel
from firedantic import Model

class Owner(BaseModel):
    first_name: str
    last_name: str


class Company(Model):
    __collection__ = "companies"
    company_id: str
    owner: Owner

# Now you can use the model to save it to Firestore
owner = Owner(first_name="John", last_name="Doe")
company = Company(company_id="1234567-8", owner=owner)
company.save()

# Access the company id
print(company.id)

# Reloads model data from the database
company.reload()
```

Querying is done via a MongoDB-like `find()`:

```python
from firedantic import Model
import firedantic.operators as op
from google.cloud.firestore import Query

class Product(Model):
    __collection__ = "products"
    product_id: str
    stock: int
    unit_value: int


Product.find({"product_id": "abc-123"})
Product.find({"stock": {">=": 3}})
# or
Product.find({"stock": {op.GTE: 3}})
Product.find({"stock": {">=": 1}}, order_by=[('unit_value', Query.ASCENDING)], limit=25, offset=50)
Product.find(order_by=[('unit_value', Query.ASCENDING), ('stock', Query.DESCENDING)], limit=2)

# Count matching documents without reading them
Product.count()
Product.count({"stock": {op.GTE: 3}})

# Fetch several documents by ID in one request; missing ones are left out
Product.get_by_ids(["id-1", "id-2"])
```

The query operators are found at
[https://firebase.google.com/docs/firestore/query-data/queries#query_operators](https://firebase.google.com/docs/firestore/query-data/queries#query_operators).
Models extending `BareModel` with a custom document ID field use `get_by_doc_ids()`
instead of `get_by_ids()`.

#### Atomic increments

`increment()` atomically adds to a numeric field without reading and saving the whole
model. The field is a Firestore field path, so it uses field aliases and dots for nested
fields. If the stored value is missing or not a number, Firestore sets it to the amount.

```python
product = Product(product_id="abc-123", stock=10, unit_value=5)
product.save()

product.increment("stock", 5)
assert product.stock == 15
product.increment("stock", -3)
assert product.stock == 12
```

The model instance gets the same change, but not other writes to the field, so use
`reload()` to see the stored value. In a transaction the instance is left unchanged,
since the write only happens when the transaction commits.

### Async Usage

Firedantic can also be used in an async way, like this:

#### Async Model Example

```python
class Person(AsyncModel):
    __collection__ = "persons"
    name: str

async def main():
    alice = Person(name="Alice")
    await alice.save()
    bob = Person(name="Bob")
    await bob.save()

    found_alice = await Person.find_one({"name": "Alice"})
    print(f"Found Alice: {found_alice.id}")
    assert alice.id == found_alice.id

    found_bob = await Person.get_by_id(bob.id)
    assert bob.id == found_bob.id
    print(f"Found Bob: {found_bob.id}")

    await alice.delete()
    await bob.delete()

if __name__ == "__main__":
    asyncio.run(main())
```

## Subcollections

Subcollections in Firestore are basically dynamically named collections.

Firedantic supports them via the `SubCollection` and `SubModel` classes, by creating
dynamic classes with collection name determined based on the "parent" class it is in
reference to using the `model_for()` method.

```python
from typing import Optional, Type
from firedantic import AsyncModel, AsyncSubCollection, AsyncSubModel, ModelNotFoundError

class UserStats(AsyncSubModel):
    id: Optional[str] = None
    purchases: int = 0

    class Collection(AsyncSubCollection):
        # Can use any properties of the "parent" model
        __collection_tpl__ = "users/{id}/stats"

class User(AsyncModel):
    __collection__ = "users"
    name: str

async def get_user_purchases(user_id: str, period: str = "2021") -> int:
    user = await User.get_by_id(user_id)
    stats_model: Type[UserStats] = UserStats.model_for(user)
    try:
        stats = await stats_model.get_by_id(period)
    except ModelNotFoundError:
        stats = stats_model()
    return stats.purchases

```

## Collection group queries

A
[collection group query](https://firebase.google.com/docs/firestore/query-data/queries#collection-group-query)
searches every collection with the same ID, for example the `surveys` subcollection of
every animal. Any model can run one with `find_in_group()` and `find_one_in_group()`,
which take the same arguments as `find()` and `find_one()`.

Firestore matches collections by their last path segment only, so `animals/*/surveys`,
`sites/*/surveys` and a top-level `surveys` collection are all in the same group.
Firedantic limits the query to documents below the model's top-level collection
(including the configured prefix), and skips any remaining documents whose path doesn't
match the collection template, such as `animals/*/visits/*/surveys`. When documents are
skipped, more are fetched to fill the page, so a page shorter than `limit` always means
there are no more results.

To avoid reading documents that are then skipped, set `__discriminator__` to a field
whose default value identifies the model. It is added to the query as an equality
filter. If the field doesn't exist or has no default, defining the model raises a
`ValueError`.

```python
from typing import Literal, Optional
from firedantic import AsyncModel, AsyncSubCollection, AsyncSubModel

class Animal(AsyncModel):
    __collection__ = "animals"
    name: str

class AnimalSurvey(AsyncSubModel):
    __discriminator__ = "kind"

    id: Optional[str] = None
    kind: Literal["animal_survey"] = "animal_survey"
    status: str

    class Collection(AsyncSubCollection):
        __collection_tpl__ = "animals/{id}/surveys"

async def close_open_surveys() -> None:
    # Open surveys of every animal
    surveys = await AnimalSurvey.find_in_group({"status": "open"})
    for survey in surveys:
        print(survey.get_parent_id(), survey.get_document_path())
        # The models remember where they were loaded from, so this saves them back
        # under the right animal
        survey.status = "closed"
        await survey.save()
```

The collection group ID is the last segment of the collection path (`surveys` above).
Set `__collection_group__` on the model to override it.

### Pagination

Pass the last model of the previous page as `start_after` to get the next page. Its
`get_document_path()` works too, for example when the cursor goes through an API:

```python
from google.cloud.firestore import Query

page = await AnimalSurvey.find_in_group(order_by=[("status", Query.ASCENDING)], limit=20)
next_page = await AnimalSurvey.find_in_group(
    order_by=[("status", Query.ASCENDING)], limit=20, start_after=page[-1]
)
```

`offset` is supported as well, but Firestore bills for every document it skips, and the
offset also counts documents skipped by the path check.

### Indexes

Collection group queries need indexes with collection group scope, which can be defined
with `collection_group_index(...)` as described below. Filtering on a single field also
needs a single-field index with collection group scope, which Firestore doesn't create
automatically. Because the path restriction is a range filter on the document name,
Firestore can't combine single-field indexes for these queries: every filter or sort
shape used with `find_in_group()`, including plain equality filters, needs its own
composite index with collection group scope, with `__discriminator__` as the first field
if set. When a required index is missing, the error message from Firestore includes a
link to create it.

## Composite Indexes and TTL Policies

Firedantic supports defining and automatically creating Composite Indexes and TTL
Policies for your Firestore models. These can be created using either:

- The new `Configuration` class (recommended)
- The legacy `configure()` method (deprecated but still supported)

### Defining Composite Indexes

Composite indexes are defined on your model using the `__composite_indexes__` attribute.

It must be a list of composite index definitions created using:

- `collection_index(...)` – for single-collection queries
- `collection_group_index(...)` – for collection group queries

Each index definition takes an arbitrary number of (field_name, order) tuples. Order
must be `Query.ASCENDING` or `Query.DESCENDING`.

#### Example:

```python
__composite_indexes__ = [
    collection_index(("content", Query.ASCENDING), ("expire", Query.DESCENDING)),
    collection_group_index(("content", Query.DESCENDING), ("expire", Query.ASCENDING)),
]
```

### Defining TTL Policies

TTL (Time-To-Live) policies are defined using the `__ttl_field__` attribute.

Rules:

- The field must be a datetime object
- The field name must be assigned to `__ttl_field__`
- TTL policies cannot be created in the Firestore Emulator

#### Example:

```python
__ttl_field__ = "expire"
```

#### Recommended Usage (New Configuration API)

All index and TTL setup functions now automatically resolve:

- The correct project
- The correct database
- The correct admin client …based on each model’s `__db_config__`

This means you no longer need to pass projects, databases, or admin clients manually--
And further, you can maintain multiple of each within your app.

#### Sync Example (Recommended)

```python
from datetime import datetime
from google.cloud.firestore import Query

from firedantic import (
    Model,
    collection_index,
    collection_group_index,
    get_all_subclasses,
    set_up_composite_indexes_and_ttl_policies,
)
from firedantic.configurations import configuration

class ExpiringModel(Model):
    __collection__ = "expiringModel"
    __ttl_field__ = "expire"
    __composite_indexes__ = [
        collection_index(("content", Query.ASCENDING), ("expire", Query.DESCENDING)),
        collection_group_index(("content", Query.DESCENDING), ("expire", Query.ASCENDING)),
    ]

    expire: datetime
    content: str

def main():
    configuration.add(
        name="(default)",
        project="my-project",
        prefix="firedantic-test-",
        # credentials=... optional
    )

    set_up_composite_indexes_and_ttl_policies(
        models=get_all_subclasses(Model),
    )

if __name__ == "__main__":
    main()
```

#### Async Example (Recommended)

```python
import asyncio
from datetime import datetime
from google.cloud.firestore import Query

from firedantic import (
    AsyncModel,
    async_set_up_composite_indexes_and_ttl_policies,
    collection_index,
    collection_group_index,
    get_all_subclasses,
)
from firedantic.configurations import configuration

class ExpiringModel(AsyncModel):
    __collection__ = "expiringModel"
    __ttl_field__ = "expire"
    __composite_indexes__ = [
        collection_index(("content", Query.ASCENDING), ("expire", Query.DESCENDING)),
        collection_group_index(("content", Query.DESCENDING), ("expire", Query.ASCENDING)),
    ]

    expire: datetime
    content: str

async def main():
    configuration.add(
        project="my-project",
        prefix="firedantic-test-",
    )

    await async_set_up_composite_indexes_and_ttl_policies(
        models=get_all_subclasses(AsyncModel),
    )

if __name__ == "__main__":
    asyncio.run(main())
```

#### Legacy Usage (Still Supported, Deprecated)

The old method using `configure()` and manually passing Firestore Admin clients is still
supported but deprecated.

##### Composite indexes

Composite indexes of a collection are defined in `__composite_indexes__`, which is a
list of all indexes to be created.

To define an index, you can use `collection_index` or `collection_group_index`,
depending on the query scope of the index. Each of these takes in an arbitrary amount of
tuples, where the first element is the field name and the second is the order
(`ASCENDING`/`DESCENDING`).

The `set_up_composite_indexes` and `async_set_up_composite_indexes` functions are used
to create indexes.

For more details, see the example further down.

##### TTL Policies

The field used for the TTL policy should be a datetime field and the name of the field
should be defined in `__ttl_field__`. The `set_up_ttl_policies` and
`async_set_up_ttl_policies` functions are used to set up the policies.

Note: The TTL policies can not be set up in the Firestore emulator.

##### Examples

Below are examples (both sync and async) to show how to use Firedantic to set up
composite indexes and TTL policies with the legacy format.

The examples use `async_set_up_composite_indexes_and_ttl_policies` and
`set_up_composite_indexes_and_ttl_policies` functions to set up both composite indexes
and TTL policies. However, you can use separate functions to set up only either one of
them.

###### Legacy Sync Example

```python
from datetime import datetime
from google.cloud.firestore import Client, Query
from google.cloud.firestore_admin_v1 import FirestoreAdminClient


from firedantic import (
    Model,
    collection_index,
    collection_group_index,
    configure,
    get_all_subclasses,
    set_up_composite_indexes_and_ttl_policies,
)


class ExpiringModel(Model):
    __collection__ = "expiringModel"
    __ttl_field__ = "expire"
    __composite_indexes__ = [
        collection_index(("content", Query.ASCENDING), ("expire", Query.DESCENDING)),
        collection_group_index(("content", Query.DESCENDING), ("expire", Query.ASCENDING)),
    ]

    expire: datetime
    content: str


def main():
    configure(Client(), prefix="firedantic-test-")
    set_up_composite_indexes_and_ttl_policies(
        gcloud_project="my-project",
        models=get_all_subclasses(Model),
        client=FirestoreAdminClient(),
    )

if __name__ == "__main__":
    main()
```

###### Legacy Async Example

```python
import asyncio
from datetime import datetime
from google.cloud.firestore import AsyncClient, Query
from google.cloud.firestore_admin_v1.services.firestore_admin import FirestoreAdminAsyncClient

from firedantic import (
    AsyncModel,
    async_set_up_composite_indexes_and_ttl_policies,
    collection_index,
    collection_group_index,
    configure,
    get_all_subclasses,
)


class ExpiringModel(AsyncModel):
    __collection__ = "expiringModel"
    __ttl_field__ = "expire"
    __composite_indexes__ = [
        collection_index(("content", Query.ASCENDING), ("expire", Query.DESCENDING)),
        collection_group_index(("content", Query.DESCENDING), ("expire", Query.ASCENDING)),
    ]

    expire: datetime
    content: str


async def main():
    configure(AsyncClient(), prefix="firedantic-test-")
    await async_set_up_composite_indexes_and_ttl_policies(
        gcloud_project="my-project",
        models=get_all_subclasses(AsyncModel),
        client=FirestoreAdminAsyncClient(),
    )

if __name__ == "__main__":
    asyncio.run(main())
```

## Transactions

Firedantic has basic support for
[Firestore Transactions](https://firebase.google.com/docs/firestore/manage-data/transactions).
The following methods can be used in a transaction for both **sync** and **async**
models:

- `Model.count(transaction=transaction)`
- `Model.delete(transaction=transaction)`
- `Model.find_one(transaction=transaction)`
- `Model.find(transaction=transaction)`
- `Model.get_by_doc_id(transaction=transaction)`
- `Model.get_by_doc_ids(transaction=transaction)`
- `Model.get_by_id(transaction=transaction)`
- `Model.get_by_ids(transaction=transaction)`
- `Model.increment(field, amount, transaction=transaction)`
- `Model.reload(transaction=transaction)`
- `Model.save(transaction=transaction)`
- `SubModel.get_by_id(transaction=transaction)`
- `SubModel.get_by_ids(transaction=transaction)`

When using transactions, note that read operations must come before write operations.

### Recommended Usage (New Configuration Class)

With the new `Configuration` system, transactions automatically use the configured:

- Project
- Database
- Client based on the active configuration.

### Transaction examples

#### Sync Transaction Example (Recommended)

```python
from firedantic import Model, get_transaction
from firedantic.configurations import configuration
from google.cloud.firestore_v1 import transactional, Transaction


# Configure once
configuration.add(
    project="firedantic-test",
    prefix="firedantic-test-",
)


class City(Model):
    __collection__ = "cities"
    population: int

    def increment_population(self, increment: int = 1):
        @transactional
        def _increment_population(transaction: Transaction) -> None:
            self.reload(transaction=transaction)
            self.population += increment
            self.save(transaction=transaction)

        t = get_transaction()
        _increment_population(transaction=t)


def main():
    @transactional
    def decrement_population(
        transaction: Transaction, city: City, decrement: int = 1
    ):
        city.reload(transaction=transaction)
        city.population = max(0, city.population - decrement)
        city.save(transaction=transaction)

    c = City(id="SF", population=1)
    c.save()

    c.increment_population(increment=1)
    assert c.population == 2

    t = get_transaction()
    decrement_population(transaction=t, city=c, decrement=5)
    assert c.population == 0


if __name__ == "__main__":
    main()
```

### Legacy Usage (Still Supported, Deprecated)

The original configure() method and manual client wiring still works, but is now
deprecated in favor of `Configuration`.

#### Legacy Async Transaction Example

In this example (async and sync version of it below), we are updating a `City` to
increment and decrement the population of it, both using an instance method and a
standalone function. Please note that the `@async_transactional` and `@transactional`
decorators always expect the first argument of the wrapped function to be `transaction`;
i.e. you can not directly wrap an instance method that has `self` as the first argument
or a class method that has `cls` as the first argument.

```python
import asyncio
from os import environ
from unittest.mock import Mock

import google.auth.credentials
from google.cloud.firestore import AsyncClient
from google.cloud.firestore_v1 import async_transactional, AsyncTransaction

from firedantic import AsyncModel, configure, get_async_transaction

# Firestore emulator must be running if using locally.
if environ.get("FIRESTORE_EMULATOR_HOST"):
    client = AsyncClient(
        project="firedantic-test",
        credentials=Mock(spec=google.auth.credentials.Credentials),
    )
else:
    client = AsyncClient()

configure(client, prefix="firedantic-test-")


class City(AsyncModel):
    __collection__ = "cities"
    population: int

    async def increment_population(self, increment: int = 1):
        @async_transactional
        async def _increment_population(transaction: AsyncTransaction) -> None:
            await self.reload(transaction=transaction)
            self.population += increment
            await self.save(transaction=transaction)

        t = get_async_transaction()
        await _increment_population(transaction=t)


async def main():
    @async_transactional
    async def decrement_population(
        transaction: AsyncTransaction, city: City, decrement: int = 1
    ):
        await city.reload(transaction=transaction)
        city.population = max(0, city.population - decrement)
        await city.save(transaction=transaction)

    c = City(id="SF", population=1)
    await c.save()
    await c.increment_population(increment=1)
    assert c.population == 2

    t = get_async_transaction()
    await decrement_population(transaction=t, city=c, decrement=5)
    assert c.population == 0


if __name__ == "__main__":
    asyncio.run(main())
```

#### Legacy Sync Transaction Example

```python
from os import environ
from unittest.mock import Mock

import google.auth.credentials
from google.cloud.firestore import Client
from google.cloud.firestore_v1 import transactional, Transaction

from firedantic import Model, configure, get_transaction

# Firestore emulator must be running if using locally.
if environ.get("FIRESTORE_EMULATOR_HOST"):
    client = Client(
        project="firedantic-test",
        credentials=Mock(spec=google.auth.credentials.Credentials),
    )
else:
    client = Client()

configure(client, prefix="firedantic-test-")


class City(Model):
    __collection__ = "cities"
    population: int

    def increment_population(self, increment: int = 1):
        @transactional
        def _increment_population(transaction: Transaction) -> None:
            self.reload(transaction=transaction)
            self.population += increment
            self.save(transaction=transaction)

        t = get_transaction()
        _increment_population(transaction=t)


def main():
    @transactional
    def decrement_population(
        transaction: Transaction, city: City, decrement: int = 1
    ):
        city.reload(transaction=transaction)
        city.population = max(0, city.population - decrement)
        city.save(transaction=transaction)

    c = City(id="SF", population=1)
    c.save()
    c.increment_population(increment=1)
    assert c.population == 2

    t = get_transaction()
    decrement_population(transaction=t, city=c, decrement=5)
    assert c.population == 0


if __name__ == "__main__":
    main()
```

## Development

PRs are welcome!

### Prerequisites:

- [Node 22](https://nodejs.org/en/download)
- [Pre-commit](https://pre-commit.com/#install)
- [Python 3.10+](https://wiki.python.org/moin/BeginnersGuide/Download)
- [Poetry](https://python-poetry.org/docs/#installation)
- [Google Firebase Emulator Suite](https://firebase.google.com/docs/emulator-suite/install_and_configure#install_the_local_emulator_suite)

### Running Firestore emulator

To run the Firestore emulator locally you will need:

- [Firebase CLI](https://firebase.google.com/docs/cli)

To install the `firebase` CLI run:

```bash
npm install -g firebase-tools
```

Run the Firestore emulator with a predictable port:

```bash
./start_emulator.sh
# or on Windows run the .bat file
start_emulator
```

### About sync and async versions of library

Although this library provides both sync and async versions of models, please keep in
mind that you need to explicitly maintain only async version of it. The synchronous
version is generated automatically by invoke task:

```bash
poetry run invoke unasync
```

We decided to go this way in order to:

- make sure both versions have the same API
- reduce human error factor
- avoid working on two code bases at the same time to reduce maintenance effort

Thus, please make sure you don't modify any of files under
[firedantic/\_sync](./firedantic/_sync) and
[firedantic/tests/tests_sync](./firedantic/tests/tests_sync) by hands. `unasync` is also
running as part of pre-commit hooks, but in order to run the latest version of tests you
have to run it manually.

### Generating changelog

After you have increased the version number in [pyproject.toml](pyproject.toml), please
run the following command to generate a changelog placeholder and fill in the relevant
information about the release in [CHANGELOG.md](CHANGELOG.md):

```bash
poetry run invoke make-changelog
```

### Running Tests

To run tests locally, you should first:

```bash
poetry install
poetry run invoke test
```

\*Note, when new functions, comments, variables etc. are added that will span across
sync and async directories, be sure to first declare the replacements in `unasync.py`
before running `poetry run invoke test`. I.e. indicating a replacement of 'async_client'
with 'client' text across both directories.

\*Note, the emulator must be set and running for all tests to pass.

### Running Integration Tests

#### Environment and configuration

- Ensure firestore emulator is running in another terminal window:
  - `./start_emulator.sh`

#### Files and purpose (replace placeholders with real filenames)

- `integration_tests/configure_firestore_db_clients.py` — Purpose: shows how to create
  and connect to various db clients.
- `integration_tests/full_sync_flow.py` — Purpose: configures clients, saves data to db,
  finds the data, and deletes all data in a sync fashion.
- `integration_tests/full_async_flow.py` — Purpose: configures async clients, saves data
  to db, finds the data, and deletes all data in an async fashion.
- `integration_tests/full_readme_examples.py` - Purpose: intended to run all examples
  shown in the readme, new and legacy.

#### How to run

Run each individual test file:

- `poetry run python integration_tests/configure_firestore_db_clients.py`
- `poetry run python integration_tests/full_sync_flow.py`
- `poetry run python integration_tests/full_async_flow.py`
- `poetry run python integration_tests/full_readme_examples.py`

or run all:

```bash
poetry run invoke integration
```

#### What to expect

- For the configure_firestore_db_clients test, you should expect to see the following:
  `All configure_firestore_db_client tests passed!`

- For the `full_sync_flow` and `full_async_flow`, you should expect to see the following
  output: \
  You can readily play around with the models to update the data as desired.

```
Number of company owners with first name: 'Bill': 1

Number of companies with id: '1234567-7': 1

Number of company owners with first name: 'John': 1

Number of companies with id: '1234567-8a': 1

Number of company owners with first name: 'Alice': 1

Number of billing companies with id: '1234567-8c': 1

Number of billing accounts with billing_id: 801048: 1
```

## License

This code is released under the BSD 3-Clause license. Details in the
[LICENSE](./LICENSE) file.
