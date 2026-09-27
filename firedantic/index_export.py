"""
Export the indexes and TTL policies that models declare in the `firestore.indexes.json`
format of the Firebase CLI, and merge them into an existing file.
"""

import copy
import importlib
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple, Type

from pydantic import BaseModel

from firedantic.common import AUTOMATIC_FIELD_INDEXES, get_field_index_entries
from firedantic.configurations import configuration
from firedantic.exceptions import CollectionNotDefined
from firedantic.utils import get_all_subclasses

IndexesJson = Dict[str, List[Dict[str, Any]]]


def export_firestore_indexes(
    models: Iterable[Type[BaseModel]], database: Optional[str] = None
) -> IndexesJson:
    """
    Returns the indexes and TTL policies that the models declare, as the contents of a
    `firestore.indexes.json` file:

    - `__composite_indexes__` become `indexes` entries.
    - `__field_indexes__` and `__ttl_field__` become `fieldOverrides` entries. They also
      list Firestore's automatic indexes for the field, since an override replaces them.

    Collection names include the prefix of each model's configuration. A
    `firestore.indexes.json` file is for one database, so the models must use one
    database, or `database` must be given to pick the models that use it.

    :param models: Models to export. Models that declare nothing are skipped.
    :param database: Only export the models whose configuration uses this database.
    :return: A dict with `indexes` and `fieldOverrides` lists.
    :raise ValueError: If the models use several databases and `database` isn't given.
    """
    return merge_firestore_indexes({}, models, database)[0]


def merge_firestore_indexes(
    existing: Dict[str, Any],
    models: Iterable[Type[BaseModel]],
    database: Optional[str] = None,
) -> Tuple[IndexesJson, List[str]]:
    """
    Adds the indexes and TTL policies that the models declare to the contents of an
    existing `firestore.indexes.json` file, like `export_firestore_indexes()` does.
    Entries already in the file are kept as they are, and missing declared indexes are
    added to existing field overrides.

    :param existing: The parsed contents of the existing file.
    :param models: Models whose declarations to add.
    :param database: Only use the models whose configuration uses this database.
    :return: The merged contents, and a description of each added entry or setting.
    :raise ValueError: If the models use several databases and `database` isn't given.
    """
    merged: Dict[str, Any] = copy.deepcopy(existing)
    indexes: List[Dict[str, Any]] = merged.setdefault("indexes", [])
    overrides: List[Dict[str, Any]] = merged.setdefault("fieldOverrides", [])
    changes: List[str] = []

    for model in _select_models(models, database):
        try:
            collection_group = model.get_collection_group_id()  # type: ignore[attr-defined]
        except CollectionNotDefined:
            # Base classes that declare indexes for their subclasses
            continue

        for index in getattr(model, "__composite_indexes__", None) or []:
            entry = {
                "collectionGroup": collection_group,
                "queryScope": index.query_scope,
                # Fields may be IndexField tuples or plain (name, order) tuples
                "fields": [{"fieldPath": f[0], "order": f[1]} for f in index.fields],
            }
            if not any(_composite_key(e) == _composite_key(entry) for e in indexes):
                indexes.append(entry)
                changes.append(_describe_composite(entry))

        wanted: Dict[str, List[Dict[str, str]]] = {}
        for field_index in getattr(model, "__field_indexes__", None) or []:
            wanted.setdefault(field_index.field_path, []).extend(
                get_field_index_entries(field_index)
            )
        ttl_field = getattr(model, "__ttl_field__", None)
        if ttl_field:
            wanted.setdefault(ttl_field, [])

        for field_path, field_entries in wanted.items():
            override = _find_override(overrides, collection_group, field_path)
            name = f"field override for {collection_group}.{field_path}"
            if override is None:
                override = {
                    "collectionGroup": collection_group,
                    "fieldPath": field_path,
                    "indexes": list(AUTOMATIC_FIELD_INDEXES),
                }
                if field_path == ttl_field:
                    override["ttl"] = True
                overrides.append(override)
                changes.append(name)
                new = True
            else:
                new = False
            current = override.setdefault("indexes", [])
            for entry in field_entries:
                if not any(_field_index_key(e) == _field_index_key(entry) for e in current):
                    current.append(entry)
                    if not new:
                        changes.append(f"{_describe_field_index(entry)} in {name}")
            if field_path == ttl_field and not override.get("ttl"):
                override["ttl"] = True
                changes.append(f"TTL in {name}")

    indexes.sort(key=_composite_key)
    overrides.sort(key=lambda o: (o.get("collectionGroup", ""), o.get("fieldPath", "")))
    return merged, changes


def find_models(module_names: Iterable[str]) -> List[Type[BaseModel]]:
    """
    Imports the modules and returns the firedantic models defined in them or their
    submodules, sorted by module and name.
    """
    from firedantic._async.model import AsyncBareModel
    from firedantic._sync.model import BareModel

    prefixes = []
    for name in module_names:
        importlib.import_module(name)
        prefixes.append(name)

    found: List[Type[BaseModel]] = []
    for base in (BareModel, AsyncBareModel):
        for model in get_all_subclasses(base):
            module = model.__module__
            if model not in found and any(
                module == p or module.startswith(p + ".") for p in prefixes
            ):
                found.append(model)
    return sorted(found, key=lambda m: (m.__module__, m.__qualname__))


def _declares_anything(model: Type[BaseModel]) -> bool:
    return bool(
        getattr(model, "__composite_indexes__", None)
        or getattr(model, "__field_indexes__", None)
        or getattr(model, "__ttl_field__", None)
    )


def _select_models(
    models: Iterable[Type[BaseModel]], database: Optional[str]
) -> List[Type[BaseModel]]:
    selected = []
    databases: Set[str] = set()
    for model in models:
        if not _declares_anything(model):
            continue
        config_name = getattr(model, "__db_config__", "(default)")
        model_database = configuration.get_config(config_name).database
        if database is not None and model_database != database:
            continue
        databases.add(model_database)
        selected.append(model)
    if len(databases) > 1:
        raise ValueError(
            f"The models use several databases ({', '.join(sorted(databases))}). A "
            "firestore.indexes.json file is for one database, so pick one."
        )
    return selected


def _find_override(
    overrides: List[Dict[str, Any]], collection_group: str, field_path: str
) -> Optional[Dict[str, Any]]:
    for override in overrides:
        if (
            override.get("collectionGroup") == collection_group
            and override.get("fieldPath") == field_path
        ):
            return override
    return None


def _composite_key(entry: Dict[str, Any]) -> Tuple[Any, ...]:
    fields = [
        (f.get("fieldPath"), f.get("order") or f.get("arrayConfig") or f.get("vectorConfig"))
        for f in entry.get("fields", [])
    ]
    # Firestore adds __name__ in the direction of the last field, so files may or may
    # not list it
    if len(fields) > 1 and fields[-1][0] == "__name__" and fields[-1][1] == fields[-2][1]:
        fields = fields[:-1]
    return (
        entry.get("collectionGroup", ""),
        entry.get("queryScope", "COLLECTION"),
        tuple(str(f) for f in fields),
    )


def _field_index_key(entry: Dict[str, Any]) -> Tuple[str, str, str]:
    return (
        entry.get("queryScope", "COLLECTION"),
        entry.get("order", ""),
        entry.get("arrayConfig", ""),
    )


def _describe_composite(entry: Dict[str, Any]) -> str:
    fields = ", ".join(f"{f['fieldPath']} {f['order']}" for f in entry["fields"])
    return f"{entry['queryScope']} index on {entry['collectionGroup']} ({fields})"


def _describe_field_index(entry: Dict[str, str]) -> str:
    kind = entry.get("order") or f"{entry.get('arrayConfig')}"
    return f"{entry['queryScope']} {kind} index"
