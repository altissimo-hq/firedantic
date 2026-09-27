from logging import getLogger
from typing import Dict, Iterable, List, Optional, Set, Type

from google.api_core.operation import Operation
from google.cloud.firestore_admin_v1 import (
    CreateIndexRequest,
    Field,
    Index,
    ListIndexesRequest,
)
from google.cloud.firestore_admin_v1.services.firestore_admin import (
    FirestoreAdminClient,
)

from firedantic._sync.model import BareModel
from firedantic._sync.ttl_policy import set_up_ttl_policies
from firedantic.common import (
    AUTOMATIC_FIELD_INDEXES,
    IndexDefinition,
    IndexField,
    get_field_index_entries,
)
from firedantic.configurations import configuration

logger = getLogger("firedantic")


def get_existing_indexes(client: FirestoreAdminClient, path: str) -> Set[IndexDefinition]:
    """
    Get existing database indexes and return a set of them
    for easy comparison with other indexes

    :param client: The Firestore admin client.
    :param path: Index path in Firestore.
    :return: Set of IndexDef tuples
    """
    raw_indexes = []
    request = ListIndexesRequest({"parent": path})
    operation = client.list_indexes(request=request)
    for page in operation.pages:
        raw_indexes.extend(list(page.indexes))

    indexes = set()
    for raw_index in raw_indexes:
        # apparently `list_indexes` returns all indexes in all collections; match the
        # whole collection group ID, so "surveys" doesn't match "surveys_archive"
        if not raw_index.name.startswith(path + "/"):
            continue
        query_scope = raw_index.query_scope.name
        fields = tuple(
            IndexField(name=f.field_path, order=f.order.name)  # noqa
            for f in raw_index.fields
            if f.field_path != "__name__"
        )
        indexes.add(IndexDefinition(query_scope=query_scope, fields=fields))
    return indexes


def create_composite_index(
    client: FirestoreAdminClient,
    index: IndexDefinition,
    path: str,
) -> Operation:
    """
    Create a composite index in Firestore

    :param client: The Firestore admin client.
    :param index: Index definition.
    :param path: Index path in Firestore.
    :return: Operation that was launched to create the index.
    """
    request = CreateIndexRequest(
        {
            "parent": path,
            "index": Index(
                {
                    "query_scope": index.query_scope,
                    "fields": [
                        {"field_path": field[0], "order": field[1]} for field in list(index.fields)
                    ],
                }
            ),
        }
    )
    return client.create_index(request=request)


def set_up_composite_indexes(
    gcloud_project: Optional[str],
    models: Iterable[Type[BareModel]],
    database: Optional[str] = None,
    client: Optional[FirestoreAdminClient] = None,
) -> List[Operation]:
    """
    Set up composite indexes for models.

    :param gcloud_project: The technical name of the project in Google Cloud.
    :param models: Models for which to set up composite indexes.
    :param database: The Firestore database. Defaults to the database of each model's
        configuration.
    :param client: The Firestore admin client.
    :return: List of operations that were launched to create indexes.
    """
    if not client:
        client = FirestoreAdminClient()

    operations = []
    for model in models:
        if not getattr(model, "__composite_indexes__", None):
            continue

        # Resolve config name: prefer model __db_config__ if present; else default
        config_name = getattr(model, "__db_config__", "(default)")

        # If caller did not pass gcloud_project or database, get them from config
        project = gcloud_project or configuration.get_config(config_name).project
        model_database = database or configuration.get_config(config_name).database

        # For sub-models this is the last segment of the collection template
        collection_group = model.get_collection_group_id()
        path = f"projects/{project}/databases/{model_database}/collectionGroups/{collection_group}"

        indexes_in_db = get_existing_indexes(client, path=path)
        model_indexes = set(model.__composite_indexes__)  # type: ignore[arg-type]

        existing_indexes = indexes_in_db.intersection(model_indexes)
        new_indexes = model_indexes.difference(indexes_in_db)

        for index in existing_indexes:
            logger.debug(
                "Composite index already exists in DB: %s, collection: %s",
                index,
                collection_group,
            )

        for index in new_indexes:
            logger.info(
                "Creating new composite index: %s, collection: %s",
                index,
                collection_group,
            )
            operation = create_composite_index(client, index, path)
            operations.append(operation)

    return operations


def set_up_composite_indexes_and_ttl_policies(
    gcloud_project: str,
    models: Iterable[Type[BareModel]],
    database: Optional[str] = None,
    client: Optional[FirestoreAdminClient] = None,
) -> List[Operation]:
    """
    Set up the composite indexes, field indexes and TTL policies that are defined in
    the models.

    :param gcloud_project: The technical name of the project in Google Cloud.
    :param models: Models for which to set up indexes and TTL policies.
    :param database: The Firestore database. Defaults to the database of each model's
        configuration.
    :param client: The Firestore admin client.
    :return: List of operations that were launched.
    """
    models = list(models)
    ops = set_up_composite_indexes(gcloud_project, models, database, client)
    ops.extend(set_up_field_indexes(gcloud_project, models, database, client))
    ops.extend(set_up_ttl_policies(gcloud_project, models, database, client))
    return ops


def set_up_field_indexes(
    gcloud_project: Optional[str],
    models: Iterable[Type[BareModel]],
    database: Optional[str] = None,
    client: Optional[FirestoreAdminClient] = None,
) -> List[Operation]:
    """
    Set up the single-field indexes declared in the models' `__field_indexes__`.

    Each field gets an index override that adds the declared indexes to the field's
    current indexes. An override replaces the indexes Firestore creates automatically,
    so those are kept in it.

    :param gcloud_project: The technical name of the project in Google Cloud. Defaults
        to the project of each model's configuration.
    :param models: Models for which to set up field indexes.
    :param database: The Firestore database. Defaults to the database of each model's
        configuration.
    :param client: The Firestore admin client.
    :return: List of operations that were launched to update fields.
    """
    if not client:
        client = FirestoreAdminClient()

    operations = []
    for model in models:
        field_indexes = getattr(model, "__field_indexes__", None)
        if not field_indexes:
            continue

        config = configuration.get_config(getattr(model, "__db_config__", "(default)"))
        project = gcloud_project or config.project
        if not project:
            raise ValueError(f"No Google Cloud project given or configured for {model.__name__}")
        collection_group = model.get_collection_group_id()
        for field_index in field_indexes:
            path = client.field_path(
                project=project,
                database=database or config.database,
                collection=collection_group,
                field=field_index.field_path,
            )
            field = client.get_field({"name": path})
            # Keep the field's current indexes as they are, including kinds firedantic
            # doesn't declare, since the override replaces them
            indexes = list(field.index_config.indexes)
            if not indexes and field.index_config.uses_ancestor_config:
                indexes = [
                    _get_field_index(field_index.field_path, e) for e in AUTOMATIC_FIELD_INDEXES
                ]
            existing = [_get_field_index_entry(index) for index in indexes]
            missing = [e for e in get_field_index_entries(field_index) if e not in existing]
            if not missing:
                logger.debug(
                    "Field indexes already exist in DB: %s, collection: %s",
                    field_index,
                    collection_group,
                )
                continue

            logger.info(
                "Creating new field indexes: %s, collection: %s", field_index, collection_group
            )
            index_config = Field.IndexConfig(
                indexes=indexes + [_get_field_index(field_index.field_path, e) for e in missing]
            )
            operation = client.update_field(
                {
                    "field": Field(name=path, index_config=index_config),
                    "update_mask": {"paths": ["index_config"]},
                }
            )
            operations.append(operation)

    return operations


def _get_field_index_entry(index: Index) -> Dict[str, str]:
    """
    Returns a single-field index from the admin API as a firestore.indexes.json entry.
    Indexes that are neither ordered nor array-contains only get their query scope.
    """
    entry = {"queryScope": Index.QueryScope(index.query_scope).name}
    for field in index.fields:
        if field.array_config:
            return {"arrayConfig": "CONTAINS", **entry}
        if field.order:
            return {"order": Index.IndexField.Order(field.order).name, **entry}
    return entry


def _get_field_index(field_path: str, entry: Dict[str, str]) -> Index:
    """
    Returns a firestore.indexes.json single-field index entry as an admin API index.
    """
    field = Index.IndexField(field_path=field_path)
    if "arrayConfig" in entry:
        field.array_config = getattr(Index.IndexField.ArrayConfig, entry["arrayConfig"])
    else:
        field.order = getattr(Index.IndexField.Order, entry["order"])
    return Index(query_scope=getattr(Index.QueryScope, entry["queryScope"]), fields=[field])
