from typing import Any, Literal, NamedTuple, Optional, Tuple, Union

import pydantic
from google.cloud.firestore_v1.field_path import FieldPath

OrderDirection = Union[Literal["ASCENDING"], Literal["DESCENDING"]]

IndexField = NamedTuple("IndexField", [("name", str), ("order", OrderDirection)])

IndexDefinition = NamedTuple(
    "IndexDefinition", [("query_scope", str), ("fields", Tuple[IndexField, ...])]
)


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
