from typing import Any, Dict, List, Literal, NamedTuple, Optional, Tuple, Union

import pydantic
from google.cloud.firestore_v1.field_path import FieldPath
from google.cloud.firestore_v1.transforms import Sentinel, _NumericValue, _ValueList

OrderDirection = Union[Literal["ASCENDING"], Literal["DESCENDING"]]

IndexField = NamedTuple("IndexField", [("name", str), ("order", OrderDirection)])

IndexDefinition = NamedTuple(
    "IndexDefinition", [("query_scope", str), ("fields", Tuple[IndexField, ...])]
)


FieldIndexDefinition = NamedTuple(
    "FieldIndexDefinition", [("field_path", str), ("order", bool), ("array_contains", bool)]
)

# The single-field indexes Firestore creates automatically for each field, as
# firestore.indexes.json entries. A field override replaces them, so it must list
# them to keep them.
AUTOMATIC_FIELD_INDEXES: List[Dict[str, str]] = [
    {"order": "ASCENDING", "queryScope": "COLLECTION"},
    {"order": "DESCENDING", "queryScope": "COLLECTION"},
    {"arrayConfig": "CONTAINS", "queryScope": "COLLECTION"},
]


def collection_group_field_index(
    field_path: str, *, order: bool = True, array_contains: bool = False
) -> FieldIndexDefinition:
    """
    Declares single-field indexes with collection group scope for a field, which
    collection group queries on the field need. Firestore only creates single-field
    indexes with collection scope automatically.

    :param field_path: Firestore field path, using field aliases and dots for nested
        fields.
    :param order: Whether to add ascending and descending indexes, for filters and
        ordering on the field.
    :param array_contains: Whether to add an array-contains index, for `array_contains`
        and `array_contains_any` filters on the field.
    :return: FieldIndexDefinition tuple
    """
    if not order and not array_contains:
        raise ValueError("A field index needs order or array_contains")
    return FieldIndexDefinition(field_path, order, array_contains)


def get_field_index_entries(index: FieldIndexDefinition) -> List[Dict[str, str]]:
    """
    Returns the collection group indexes that a field index declares, as
    firestore.indexes.json entries.
    """
    entries = []
    if index.order:
        entries.append({"order": "ASCENDING", "queryScope": "COLLECTION_GROUP"})
        entries.append({"order": "DESCENDING", "queryScope": "COLLECTION_GROUP"})
    if index.array_contains:
        entries.append({"arrayConfig": "CONTAINS", "queryScope": "COLLECTION_GROUP"})
    return entries


def collection_index(*fields: IndexField) -> IndexDefinition:
    """
    Shorter way to create an index definition with collection query scope

    :param fields: Index fields, each element is a tuple of name and order
    :return: IndexDefinition tuple
    """
    return IndexDefinition(query_scope="COLLECTION", fields=fields)


def collection_group_index(*fields: IndexField) -> IndexDefinition:
    """
    Shorter way to create an index definition with collection group query scope

    :param fields: Index fields, each element is a tuple of name and order
    :return: IndexDefinition tuple
    """
    return IndexDefinition(query_scope="COLLECTION_GROUP", fields=fields)


def quote_field_names(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Quotes the keys of `data` as Firestore field paths, so that `update()` treats each
    key as one field even if it contains dots or other special characters.

    :param data: Data keyed by field names.
    :return: The same data keyed by field paths.
    """
    return {FieldPath(key).to_api_repr(): value for key, value in data.items()}


def is_transform(value: Any) -> bool:
    """
    Returns whether `value` is a Firestore transform, such as `DELETE_FIELD`,
    `SERVER_TIMESTAMP`, `ArrayUnion` or `Increment`, which Firestore applies to the
    stored value instead of storing it.
    """
    return isinstance(value, (Sentinel, _ValueList, _NumericValue))


def set_path_value(data: Dict[str, Any], path: str, value: Any) -> None:
    """
    Sets the value at the Firestore field path `path` in `data` the way Firestore's
    `update()` does: missing or non-map values along the path are replaced with maps.

    :param data: Document data, e.g. from `model_dump(by_alias=True)`.
    :param path: Firestore field path, e.g. "stock" or "stats.visits".
    :param value: Value to set.
    """
    *parents, key = FieldPath.from_string(path).parts
    target = data
    for part in parents:
        child = target.get(part)
        if not isinstance(child, dict):
            child = target[part] = {}
        target = child
    target[key] = value


def get_path_value(data: Dict[str, Any], path: str) -> Tuple[bool, Any]:
    """
    Returns whether `data` has a value at the Firestore field path `path`, and the
    value.

    :param data: Document data, e.g. from `model_dump(by_alias=True)`.
    :param path: Firestore field path, e.g. "stock" or "stats.visits".
    """
    target: Any = data
    for part in FieldPath.from_string(path).parts:
        if not isinstance(target, dict) or part not in target:
            return False, None
        target = target[part]
    return True, target


def increment_locally(model: pydantic.BaseModel, field: str, amount: Union[int, float]) -> None:
    """
    Applies a Firestore increment of the field path `field` to `model`, the way
    Firestore applies it to the stored document: a missing or non-numeric value is
    set to `amount`. Path segments are the keys `model_dump(by_alias=True)` uses, and
    paths that don't lead to a model field or dict key are ignored.

    :param model: The model instance to update.
    :param field: Firestore field path, e.g. "stock" or "stats.visits".
    :param amount: Amount to add.
    """
    *parents, key = FieldPath.from_string(field).parts
    target: Any = model
    for part in parents:
        target = _get_stored_value(target, part)
    current = _get_stored_value(target, key)
    if isinstance(current, (int, float)) and not isinstance(current, bool):
        _set_stored_value(target, key, current + amount)
    else:
        _set_stored_value(target, key, amount)


def _get_stored_field_name(model: pydantic.BaseModel, key: str) -> Optional[str]:
    """
    Returns the name of the field that `model_dump(by_alias=True)` stores under `key`.
    """
    for name, field in type(model).model_fields.items():
        if (field.serialization_alias or field.alias or name) == key:
            return name
    return None


def _get_stored_value(target: Any, key: str) -> Any:
    if isinstance(target, pydantic.BaseModel):
        name = _get_stored_field_name(target, key)
        return getattr(target, name) if name else None
    if isinstance(target, dict):
        return target.get(key)
    return None


def _set_stored_value(target: Any, key: str, value: Any) -> None:
    if isinstance(target, pydantic.BaseModel):
        name = _get_stored_field_name(target, key)
        if name:
            setattr(target, name, value)
    elif isinstance(target, dict):
        target[key] = value
