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

# OR filters take a list of filter dicts, and nest with op.AND
Product.find({op.OR: [{"stock": 0}, {"unit_value": {op.GTE: 100}}]})
Product.find(
    {
        "product_id": {op.IN: ["abc-123", "def-456"]},
        op.OR: [{"stock": 0}, {op.AND: [{"stock": {op.GTE: 10}}, {"unit_value": 5}]}],
    }
)

# Count matching documents without reading them
Product.count()
Product.count({"stock": {op.GTE: 3}})

# Sum and average a numeric field of matching documents without reading them
Product.sum("stock")
Product.avg("unit_value", {"stock": {op.GTE: 3}})

# Count, sums and averages from one query
result = Product.aggregate({"stock": {op.GTE: 3}}, sum=["stock"], avg=["unit_value"])
print(result.count, result.sum["stock"], result.avg["unit_value"])

# Fetch several documents by ID in one request; missing ones are left out
Product.get_by_ids(["id-1", "id-2"])
```

To page through results, pass the last model of the previous page as `start_after`. Its
document ID or `get_document_path()` works too, for example when the cursor goes through
an API. This is cheaper than `offset`, since Firestore bills for every document an
offset skips.

```python
page = Product.find(order_by=[("stock", Query.ASCENDING)], limit=20)
next_page = Product.find(order_by=[("stock", Query.ASCENDING)], limit=20, start_after=page[-1])
```

To go back a page, pass the first model of the current page as `end_before` with
`limit_to_last`, which returns the last results before it, still in the query's order.
`start_at` and `end_at` include the cursor document instead of stopping next to it.

```python
previous_page = Product.find(
    order_by=[("stock", Query.ASCENDING)], limit_to_last=20, end_before=page[0]
)
```

`sum()` and `avg()` take a Firestore field path, so they use field aliases and dots for
nested fields. Values that aren't numbers are ignored. The sum of no values is 0, and
the average is `None` if no matching document has the field. If the field exists but has
no numbers, the average is 0.0, because the Firestore client library reads Firestore's
null result as 0.0.

The keys of a filter dict are combined with AND. `op.OR` (`"$or"`) and `op.AND`
(`"$and"`) take a list of filter dicts, whose keys are also combined with AND, and can
be nested. Firestore's
[limits on OR queries](https://firebase.google.com/docs/firestore/query-data/queries#limits_on_or_queries)
apply. OR filters work in all methods that take a filter, including `count()`, `sum()`,
`avg()` and the collection group methods.

`aggregate()` returns the count and the sums and averages of several fields from one
query. Firestore only includes the documents that have every aggregated field, so its
count can be lower than `count()`, and the sums and averages are over those documents.
Firestore allows up to 4 sums and averages next to the count. `aggregate_in_group()`
does the same for a collection group.

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

#### Creating, merging and updating

`save()` replaces the whole stored document, so fields left out with `exclude_unset` or
`exclude_none` are removed from it. Firedantic has other writes for when that isn't
wanted:

```python
# Fails with google.api_core.exceptions.AlreadyExists if the document exists
product = Product(id="abc-123", product_id="abc-123", stock=10, unit_value=5)
product.create()

# Writes only the fields that were set and keeps the other stored fields
Product(id="abc-123", stock=7).save(exclude_unset=True, merge=True)

# Writes the current values of the given fields, and fails with
# google.api_core.exceptions.NotFound if the document doesn't exist
product.stock = 3
product.update("stock")

# Like Firestore's update(): Firestore field paths and their new values
product.update({"stock": 5, "unit_value": 6})
```

`update()` takes either model field names or a dict of changes. With field names it
writes the current value of each field as a whole. Without arguments it writes all
fields of the model, but unlike `save()` it keeps stored fields the model doesn't have.

With a dict, the keys are Firestore field paths, so they use field aliases and dots for
nested fields, like `"stats.visits"`. The changes are validated with the model before
they are written, and applied to the model instance after they are written. Firestore
transforms such as `DELETE_FIELD`, `SERVER_TIMESTAMP`, `ArrayUnion` and `Increment` are
passed through as they are. Only `Increment` is applied to the instance, so use
`reload()` to see the result of the others. In a transaction the instance is left
unchanged, since the write only happens when the transaction commits.

#### Stored values

Firestore stores strings, numbers, booleans, bytes, datetimes, maps, arrays, geo points,
document references and vectors. firedantic converts other values when it writes a model
and in filter values, so pydantic types work as fields:

| Type                    | Stored as                                           |
| ----------------------- | --------------------------------------------------- |
| `Enum`                  | its value                                           |
| `date`                  | ISO string, e.g. `"2026-10-01"`, which sorts        |
| `Decimal`               | exact string, e.g. `"12.50"`                        |
| `timedelta`             | total seconds, e.g. `2700.0`                        |
| `UUID`, `HttpUrl`, etc. | pydantic's JSON form, e.g. `"https://example.com/"` |

They all read back into the model. `Decimal` strings don't sort as numbers, so range
filters and ordering on a `Decimal` field don't work. Store money as integer cents, or
use a `field_serializer`, if you need those. A `field_serializer` or `PlainSerializer`
on a field decides how it's stored, since it runs before the conversion:

```python
from datetime import date, datetime, timezone
from pydantic import field_serializer

class Event(Model):
    __collection__ = "events"
    day: date

    @field_serializer("day")
    def store_day_as_timestamp(self, day: date) -> datetime:
        return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
```

Filter values get the default conversion, so filters on such a field need the stored
form, e.g. `Event.find({"day": datetime(2026, 10, 1, tzinfo=timezone.utc)})`.
`to_firestore_value()` does the conversion, for code that queries Firestore directly.

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

Firestore doesn't delete subcollections when their parent document is deleted, and the
documents left behind are still found by collection group queries. Use
`delete(recursive=True)` to delete a document together with everything below it:

```python
deleted = await user.delete(recursive=True)  # the user, its stats and their subcollections
print(f"Deleted {deleted} documents")
```

The documents are deleted in batches, so a recursive delete isn't atomic and can't be
used in a transaction or a batched write.

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

`count_in_group()` counts the matching documents of the group with a count aggregation
query, without reading them. It can't check document paths, so it also counts documents
that `find_in_group()` would skip, like `animals/*/visits/*/surveys`, unless the
`__discriminator__` excludes them.

```python
open_surveys = await AnimalSurvey.count_in_group({"status": "open"})
```

`sum_in_group()` and `avg_in_group()` work the same way for sums and averages.

```python
total_score = await AnimalSurvey.sum_in_group("score", {"status": "open"})
average_score = await AnimalSurvey.avg_in_group("score")
```

### Pagination

Like `find()`, `find_in_group()` takes the last model of the previous page as
`start_after`, and the other cursors and `limit_to_last` too. Its `get_document_path()`
works too, but not its document ID, since the ID alone doesn't say which collection of
the group the document is in:

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

## Missing indexes

When Firestore rejects a query because an index is missing, firedantic raises
`MissingIndexError`. It's a `FailedPrecondition`, like Firestore's own error, and its
message shows the index to add: as a `__composite_indexes__` or `__field_indexes__`
declaration where firedantic can express it, and as a `firestore.indexes.json` entry.
Single-field indexes for collection group queries are shown as a `fieldOverrides` entry
that keeps Firestore's automatic indexes for the field, since an override replaces them.

```text
Firestore needs an index for this query on collection group 'surveys'.
Add it to the model's __composite_indexes__:
    collection_group_index(("status", Query.ASCENDING), ("score", Query.DESCENDING))
Or add it to "indexes" in firestore.indexes.json:
    {
      "collectionGroup": "surveys",
      "queryScope": "COLLECTION_GROUP",
      "fields": [
        {"fieldPath": "status", "order": "ASCENDING"},
        {"fieldPath": "score", "order": "DESCENDING"}
      ]
    }
Or create it in the Firebase console: https://console.firebase.google.com/...
```

The error also has the entry as `index_json` and the declaration as `declaration`. The
Firestore emulator doesn't check indexes, so this only happens against a real database.

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

### Defining Field Indexes

Firestore creates single-field indexes automatically, but only for queries on a single
collection. Collection group queries that filter or order on a field, like
`find_in_group({"person_id": pid})`, need a single-field index with collection group
scope. Declare those with `__field_indexes__`:

```python
from firedantic import AsyncSubModel, collection_group_field_index

class Participation(AsyncSubModel):
    __field_indexes__ = [
        collection_group_field_index("person_id"),  # ascending and descending
        collection_group_field_index("tags", order=False, array_contains=True),
    ]
```

The field is a Firestore field path, so it uses aliases and dots for nested fields.
`set_up_composite_indexes_and_ttl_policies()` creates them too, or use
`set_up_field_indexes()` on its own. Each field gets an index override, which replaces
Firestore's automatic indexes for it, so the override keeps the field's current indexes
and adds the declared ones.

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

### Exporting to firestore.indexes.json

If the indexes are managed with the Firebase CLI instead, export the declarations to its
`firestore.indexes.json` file. `__composite_indexes__` become `indexes` entries, and
`__field_indexes__` and `__ttl_field__` become `fieldOverrides` entries that keep
Firestore's automatic indexes for the field.

```shell
# Add the declared indexes to the file, keeping the entries that are already there
firedantic export-indexes myapp.models --update firestore.indexes.json

# In CI: fail if the file is missing a declared index
firedantic export-indexes myapp.models --check firestore.indexes.json

# Print a new file
firedantic export-indexes myapp.models > firestore.indexes.json
```

The command imports the given modules and exports the models defined in them and their
submodules. `python -m firedantic` works too. Collection names include the configured
prefix, so configure firedantic in the listed modules, or list the module that does it
first. A file is for one database, so pick one with `--database` if the models use
several. The same is available from Python with `export_firestore_indexes(models)` and
`merge_firestore_indexes(existing, models)` in `firedantic.index_export`.

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

## Batched writes

A
[batched write](https://firebase.google.com/docs/firestore/manage-data/transactions#batched-writes)
applies several writes at once: either all of them succeed or none do. Get a batch with
`get_batch()` or `get_async_batch()`, pass it as `batch` to the write methods, and
commit it:

```python
from firedantic import get_batch

batch = get_batch()
for product in new_products:
    product.save(batch=batch)
old_product.delete(batch=batch)
batch.commit()
```

These methods take a `batch`:

- `Model.create(batch=batch)`
- `Model.delete(batch=batch)`
- `Model.increment(field, amount, batch=batch)`
- `Model.save(batch=batch)`
- `Model.update(..., batch=batch)`

As in a transaction, `save()` and `create()` set the model's ID right away, while
`update()` and `increment()` leave the model instance unchanged, since nothing is
written until the batch commits. Both helpers take the configuration name, for example
`get_batch("backup")`, and the models must use the same configuration. Unlike a
transaction, a batch can't read, and it isn't retried.

## Transactions

Firedantic has basic support for
[Firestore Transactions](https://firebase.google.com/docs/firestore/manage-data/transactions).
The following methods can be used in a transaction for both **sync** and **async**
models:

- `Model.aggregate(transaction=transaction)`
- `Model.avg(field, transaction=transaction)`
- `Model.count(transaction=transaction)`
- `Model.create(transaction=transaction)`
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
- `Model.sum(field, transaction=transaction)`
- `Model.update(*fields, transaction=transaction)`
- `Model.update(changes, transaction=transaction)`
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
