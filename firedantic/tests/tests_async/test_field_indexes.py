from typing import Any, Dict, List

import pytest
from google.cloud.firestore_admin_v1 import Field, FirestoreAdminClient, Index

from firedantic import (
    AsyncModel,
    AsyncSubCollection,
    AsyncSubModel,
    async_set_up_composite_indexes_and_ttl_policies,
    async_set_up_field_indexes,
    collection_group_field_index,
)
from firedantic.common import AUTOMATIC_FIELD_INDEXES
from firedantic.configurations import configuration

ASC = Index.IndexField.Order.ASCENDING
DESC = Index.IndexField.Order.DESCENDING
CONTAINS = Index.IndexField.ArrayConfig.CONTAINS
COLLECTION = Index.QueryScope.COLLECTION
COLLECTION_GROUP = Index.QueryScope.COLLECTION_GROUP


class Person(AsyncModel):
    __collection__ = "people"
    name: str


class Participation(AsyncSubModel):
    __field_indexes__ = [
        collection_group_field_index("person_id"),
        collection_group_field_index("tags", order=False, array_contains=True),
    ]
    person_id: str
    tags: List[str] = []

    class Collection(AsyncSubCollection):
        __collection_tpl__ = "events/{id}/participations"


def index(field_path: str, scope: int, order: int = 0, array_config: int = 0) -> Index:
    # order and array_config are a oneof, so only set the one that is used
    if array_config:
        field = Index.IndexField(field_path=field_path, array_config=array_config)
    else:
        field = Index.IndexField(field_path=field_path, order=order)
    return Index(query_scope=scope, fields=[field])


def automatic(field_path: str) -> List[Index]:
    return [
        index(field_path, COLLECTION, order=ASC),
        index(field_path, COLLECTION, order=DESC),
        index(field_path, COLLECTION, array_config=CONTAINS),
    ]


class FieldAdminClient:
    """Keeps the index config of each field, like the Firestore admin API."""

    field_path = staticmethod(FirestoreAdminClient.field_path)

    def __init__(self) -> None:
        self.fields: Dict[str, Field] = {}
        self.updates: List[Dict[str, Any]] = []

    async def get_field(self, request: Dict[str, str]) -> Field:
        name = request["name"]
        if name in self.fields:
            return self.fields[name]
        field_path = name.rsplit("/", 1)[-1]
        config = Field.IndexConfig(uses_ancestor_config=True, indexes=automatic(field_path))
        return Field(name=name, index_config=config)

    async def update_field(self, request: Dict[str, Any]) -> object:
        self.updates.append(request)
        self.fields[request["field"].name] = request["field"]
        return object()


def entries(field: Field) -> List[Dict[str, str]]:
    result = []
    for i in field.index_config.indexes:
        entry = {"queryScope": Index.QueryScope(i.query_scope).name}
        if i.fields and i.fields[0].array_config:
            result.append({"arrayConfig": "CONTAINS", **entry})
        elif i.fields and i.fields[0].order:
            result.append({"order": Index.IndexField.Order(i.fields[0].order).name, **entry})
        else:
            result.append(entry)
    return result


@pytest.fixture
def admin() -> FieldAdminClient:
    configuration.add(name="(default)", project="proj", prefix="test_")
    return FieldAdminClient()


@pytest.mark.asyncio
async def test_set_up_field_indexes(admin) -> None:
    operations = await async_set_up_field_indexes(None, [Person, Participation], client=admin)

    assert len(operations) == 2
    person_id, tags = (update["field"] for update in admin.updates)
    assert all(update["update_mask"] == {"paths": ["index_config"]} for update in admin.updates)
    base = "projects/proj/databases/(default)/collectionGroups/participations/fields"
    assert person_id.name == f"{base}/person_id"
    assert tags.name == f"{base}/tags"
    # The automatic indexes are kept, since the override replaces them
    assert entries(person_id) == AUTOMATIC_FIELD_INDEXES + [
        {"order": "ASCENDING", "queryScope": "COLLECTION_GROUP"},
        {"order": "DESCENDING", "queryScope": "COLLECTION_GROUP"},
    ]
    assert entries(tags) == AUTOMATIC_FIELD_INDEXES + [
        {"arrayConfig": "CONTAINS", "queryScope": "COLLECTION_GROUP"},
    ]
    assert all(i.fields[0].field_path == "tags" for i in tags.index_config.indexes)

    # Running it again finds the indexes and changes nothing
    assert await async_set_up_field_indexes(None, [Participation], client=admin) == []
    assert len(admin.updates) == 2


@pytest.mark.asyncio
async def test_existing_override_is_kept(admin) -> None:
    name = "projects/proj/databases/(default)/collectionGroups/participations/fields/person_id"
    # An existing override without the automatic descending index
    admin.fields[name] = Field(
        name=name,
        index_config=Field.IndexConfig(
            indexes=[
                index("person_id", COLLECTION, order=ASC),
                index("person_id", COLLECTION_GROUP, order=ASC),
            ]
        ),
    )

    class OnlyPersonId(Participation):
        __field_indexes__ = [collection_group_field_index("person_id")]

    await async_set_up_field_indexes(None, [OnlyPersonId], client=admin)

    assert entries(admin.fields[name]) == [
        {"order": "ASCENDING", "queryScope": "COLLECTION"},
        {"order": "ASCENDING", "queryScope": "COLLECTION_GROUP"},
        {"order": "DESCENDING", "queryScope": "COLLECTION_GROUP"},
    ]


@pytest.mark.asyncio
async def test_other_index_kinds_are_kept(admin) -> None:
    name = "projects/proj/databases/(default)/collectionGroups/participations/fields/person_id"
    # An index kind firedantic doesn't declare, like a vector index
    other = Index(query_scope=COLLECTION, fields=[Index.IndexField(field_path="person_id")])
    admin.fields[name] = Field(name=name, index_config=Field.IndexConfig(indexes=[other]))

    class OnlyPersonId(Participation):
        __field_indexes__ = [collection_group_field_index("person_id")]

    await async_set_up_field_indexes(None, [OnlyPersonId], client=admin)
    assert admin.fields[name].index_config.indexes[0] == other
    assert entries(admin.fields[name]) == [
        {"queryScope": "COLLECTION"},
        {"order": "ASCENDING", "queryScope": "COLLECTION_GROUP"},
        {"order": "DESCENDING", "queryScope": "COLLECTION_GROUP"},
    ]


@pytest.mark.asyncio
async def test_inherited_config_without_indexes(admin) -> None:
    async def get_field(request: Dict[str, str]) -> Field:
        config = Field.IndexConfig(uses_ancestor_config=True)
        return Field(name=request["name"], index_config=config)

    admin.get_field = get_field  # type: ignore[method-assign]

    class OnlyPersonId(Participation):
        __field_indexes__ = [collection_group_field_index("person_id")]

    await async_set_up_field_indexes(None, [OnlyPersonId], client=admin)
    assert entries(admin.updates[0]["field"])[:3] == AUTOMATIC_FIELD_INDEXES


@pytest.mark.asyncio
async def test_project_and_database_from_config(admin) -> None:
    configuration.add(name="other", project="other-proj", database="other-db")

    class OtherParticipation(Participation):
        __db_config__ = "other"
        __field_indexes__ = [collection_group_field_index("person_id")]

    await async_set_up_field_indexes(None, [OtherParticipation], client=admin)
    await async_set_up_field_indexes("given", [OtherParticipation], "given-db", client=admin)

    names = [update["field"].name for update in admin.updates]
    assert names[0].startswith("projects/other-proj/databases/other-db/")
    assert names[1].startswith("projects/given/databases/given-db/")


@pytest.mark.asyncio
async def test_set_up_everything_includes_field_indexes(admin) -> None:
    operations = await async_set_up_composite_indexes_and_ttl_policies(
        "proj", [Participation], client=admin
    )
    assert len(operations) == 2


def test_field_index_needs_an_index() -> None:
    with pytest.raises(ValueError):
        collection_group_field_index("person_id", order=False)
