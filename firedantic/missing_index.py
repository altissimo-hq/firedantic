import base64
import json
import re
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Tuple
from urllib.parse import unquote

from google.api_core.exceptions import FailedPrecondition
from google.cloud.firestore_admin_v1.types import Field, Index

from firedantic.exceptions import MissingIndexError

# Firestore's missing index errors link to the Firebase console, with the index to
# create encoded in the link: an Index message for composite indexes, or a Field
# message for single-field index overrides
_LINK = re.compile(r"https://\S+?[?&]create_(composite|exemption)=([A-Za-z0-9_\-+/=%]+)")
_COLLECTION_GROUP = re.compile(r"/collectionGroups/([^/]+)/")

# The indexes Firestore creates automatically for each field. A field override
# replaces them, so it must list them to keep them.
_AUTOMATIC_FIELD_INDEXES: List[Dict[str, str]] = [
    {"order": "ASCENDING", "queryScope": "COLLECTION"},
    {"order": "DESCENDING", "queryScope": "COLLECTION"},
    {"arrayConfig": "CONTAINS", "queryScope": "COLLECTION"},
]


@contextmanager
def report_missing_index() -> Iterator[None]:
    """
    Turns Firestore's missing index errors raised inside the block into
    `MissingIndexError`, which shows the index to add. Other errors, and missing index
    errors whose link can't be read, are raised as they are.
    """
    try:
        yield
    except FailedPrecondition as error:
        missing = get_missing_index_error(error)
        if missing is None:
            raise
        raise missing from error


def get_missing_index_error(error: FailedPrecondition) -> Optional[MissingIndexError]:
    """
    Returns a `MissingIndexError` for a Firestore missing index error, or None if the
    error has no index link that can be read.
    """
    match = _LINK.search(str(error.message))
    if match is None:
        return None
    kind, encoded = match.groups()
    try:
        data = _decode(encoded)
        if kind == "composite":
            parsed = _parse_composite(Index.deserialize(data))
        else:
            parsed = _parse_exemption(Field.deserialize(data))
    except Exception:  # noqa: BLE001 - never hide the original error behind a parse error
        return None
    if parsed is None:
        return None
    collection_group, index_json, declaration = parsed
    is_field_override = kind == "exemption"

    section = "fieldOverrides" if is_field_override else "indexes"
    lines = [
        f"Firestore needs an index for this query on collection group '{collection_group}'.",
    ]
    if declaration:
        lines.append(f"Add it to the model's __composite_indexes__:\n    {declaration}")
    lines.append(
        f'{"Or add" if declaration else "Add"} it to "{section}" in firestore.indexes.json:\n'
        + _indent(_format_json(index_json))
    )
    lines.append(f"Or create it in the Firebase console: {match.group(0)}")
    return MissingIndexError(
        "\n".join(lines),
        collection_group=collection_group,
        index_json=index_json,
        is_field_override=is_field_override,
        declaration=declaration,
        url=match.group(0),
    )


def _decode(encoded: str) -> bytes:
    encoded = unquote(encoded).replace("-", "+").replace("_", "/")
    return base64.b64decode(encoded + "=" * (-len(encoded) % 4))


def _get_collection_group(name: str) -> Optional[str]:
    match = _COLLECTION_GROUP.search(name)
    return match.group(1) if match else None


def _parse_composite(index: Index) -> Optional[Tuple[str, Dict[str, Any], Optional[str]]]:
    collection_group = _get_collection_group(index.name)
    if collection_group is None or not index.fields:
        return None
    query_scope = Index.QueryScope(index.query_scope).name

    fields = list(index.fields)
    # Firestore adds __name__ to every index, in the direction of the last field, so
    # it's only needed in the declaration when it goes the other way
    last = fields[-1]
    if last.field_path == "__name__" and len(fields) > 1:
        previous_order = fields[-2].order or Index.IndexField.Order.ASCENDING
        if last.order == previous_order:
            fields = fields[:-1]

    json_fields = []
    declared_fields: Optional[List[str]] = []
    for field in fields:
        if field.array_config:
            json_fields.append({"fieldPath": field.field_path, "arrayConfig": "CONTAINS"})
            # Composite index declarations only support ordered fields
            declared_fields = None
        else:
            order = Index.IndexField.Order(field.order).name
            json_fields.append({"fieldPath": field.field_path, "order": order})
            if declared_fields is not None:
                declared_fields.append(f'("{field.field_path}", Query.{order})')

    index_json = {
        "collectionGroup": collection_group,
        "queryScope": query_scope,
        "fields": json_fields,
    }
    declaration = None
    if declared_fields is not None:
        helper = (
            "collection_group_index" if query_scope == "COLLECTION_GROUP" else "collection_index"
        )
        declaration = f"{helper}({', '.join(declared_fields)})"
    return collection_group, index_json, declaration


def _parse_exemption(field: Field) -> Optional[Tuple[str, Dict[str, Any], Optional[str]]]:
    collection_group = _get_collection_group(field.name)
    field_path = field.name.rsplit("/fields/", 1)[-1] if "/fields/" in field.name else None
    if collection_group is None or not field_path:
        return None

    indexes = list(_AUTOMATIC_FIELD_INDEXES)
    for index in field.index_config.indexes:
        query_scope = Index.QueryScope(index.query_scope).name
        for index_field in index.fields:
            if index_field.array_config:
                entry = {"arrayConfig": "CONTAINS", "queryScope": query_scope}
            elif index_field.order:
                order = Index.IndexField.Order(index_field.order).name
                entry = {"order": order, "queryScope": query_scope}
            else:
                continue
            if entry not in indexes:
                indexes.append(entry)

    index_json = {"collectionGroup": collection_group, "fieldPath": field_path, "indexes": indexes}
    return collection_group, index_json, None


def _format_json(entry: Dict[str, Any]) -> str:
    """
    Formats an index entry like firestore.indexes.json files usually are, with each
    field or index on one line.
    """
    lines = ["{"]
    for i, (key, value) in enumerate(entry.items()):
        comma = "," if i < len(entry) - 1 else ""
        if isinstance(value, list):
            items = [f"    {json.dumps(item)}" for item in value]
            lines.append(f'  "{key}": [\n' + ",\n".join(items) + f"\n  ]{comma}")
        else:
            lines.append(f'  "{key}": {json.dumps(value)}{comma}')
    lines.append("}")
    return "\n".join(lines)


def _indent(text: str) -> str:
    return "\n".join("    " + line for line in text.splitlines())
