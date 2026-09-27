import base64
import json
from typing import List
from urllib.parse import quote

import pytest
from google.api_core.exceptions import FailedPrecondition
from google.cloud.firestore_admin_v1.types import Field, Index

from firedantic.exceptions import MissingIndexError
from firedantic.missing_index import get_missing_index_error, report_missing_index

ASC = Index.IndexField.Order.ASCENDING
DESC = Index.IndexField.Order.DESCENDING
PARENT = "projects/proj/databases/(default)/collectionGroups"


def error_for(kind: str, message_bytes: bytes, urlsafe: bool = False) -> FailedPrecondition:
    encode = base64.urlsafe_b64encode if urlsafe else base64.b64encode
    encoded = encode(message_bytes).decode().rstrip("=")
    return FailedPrecondition(
        "The query requires an index. You can create it here: "
        "https://console.firebase.google.com/v1/r/project/proj/firestore/indexes"
        f"?create_{kind}={quote(encoded)}"
    )


def composite(scope: int, fields: List[Index.IndexField]) -> bytes:
    index = Index(name=f"{PARENT}/surveys/indexes/_", query_scope=scope, fields=fields)
    data: bytes = Index.serialize(index)
    return data


def test_composite_index() -> None:
    data = composite(
        Index.QueryScope.COLLECTION_GROUP,
        [
            Index.IndexField(field_path="status", order=ASC),
            Index.IndexField(field_path="score", order=DESC),
            Index.IndexField(field_path="__name__", order=DESC),
        ],
    )
    for urlsafe in (False, True):
        error = get_missing_index_error(error_for("composite", data, urlsafe))
        assert error is not None
        assert isinstance(error, FailedPrecondition)
        assert error.collection_group == "surveys"
        assert not error.is_field_override
        # __name__ in the direction of the last field is implied
        assert error.index_json == {
            "collectionGroup": "surveys",
            "queryScope": "COLLECTION_GROUP",
            "fields": [
                {"fieldPath": "status", "order": "ASCENDING"},
                {"fieldPath": "score", "order": "DESCENDING"},
            ],
        }
        assert error.declaration == (
            'collection_group_index(("status", Query.ASCENDING), ("score", Query.DESCENDING))'
        )
        assert error.declaration in error.message
        assert '"indexes"' in error.message
        assert error.url in error.message


def test_composite_index_with_name_in_other_direction() -> None:
    data = composite(
        Index.QueryScope.COLLECTION,
        [
            Index.IndexField(field_path="score", order=ASC),
            Index.IndexField(field_path="__name__", order=DESC),
        ],
    )
    error = get_missing_index_error(error_for("composite", data))
    assert error is not None
    assert error.index_json["fields"][-1] == {"fieldPath": "__name__", "order": "DESCENDING"}
    assert error.declaration == (
        'collection_index(("score", Query.ASCENDING), ("__name__", Query.DESCENDING))'
    )


def test_composite_index_with_array_contains() -> None:
    data = composite(
        Index.QueryScope.COLLECTION,
        [
            Index.IndexField(field_path="tags", array_config=Index.IndexField.ArrayConfig.CONTAINS),
            Index.IndexField(field_path="score", order=ASC),
        ],
    )
    error = get_missing_index_error(error_for("composite", data))
    assert error is not None
    assert error.index_json["fields"][0] == {"fieldPath": "tags", "arrayConfig": "CONTAINS"}
    # Composite index declarations only support ordered fields
    assert error.declaration is None


def test_field_override() -> None:
    field = Field(
        name=f"{PARENT}/participations/fields/person_id",
        index_config=Field.IndexConfig(
            indexes=[
                Index(
                    query_scope=Index.QueryScope.COLLECTION_GROUP,
                    fields=[Index.IndexField(order=ASC)],
                )
            ]
        ),
    )
    error = get_missing_index_error(error_for("exemption", Field.serialize(field)))
    assert error is not None
    assert error.is_field_override
    assert error.declaration == 'collection_group_field_index("person_id")'
    assert "Add it to the model's __field_indexes__" in error.message
    # The override keeps the automatic indexes, which it would replace otherwise
    assert error.index_json == {
        "collectionGroup": "participations",
        "fieldPath": "person_id",
        "indexes": [
            {"order": "ASCENDING", "queryScope": "COLLECTION"},
            {"order": "DESCENDING", "queryScope": "COLLECTION"},
            {"arrayConfig": "CONTAINS", "queryScope": "COLLECTION"},
            {"order": "ASCENDING", "queryScope": "COLLECTION_GROUP"},
        ],
    }
    assert 'Or add it to "fieldOverrides"' in error.message
    # The message shows valid JSON
    start = error.message.index("{")
    end = error.message.rindex("}") + 1
    assert json.loads(error.message[start:end]) == error.index_json


def test_field_override_array_contains() -> None:
    field = Field(
        name=f"{PARENT}/participations/fields/tags",
        index_config=Field.IndexConfig(
            indexes=[
                Index(
                    query_scope=Index.QueryScope.COLLECTION_GROUP,
                    fields=[Index.IndexField(array_config=Index.IndexField.ArrayConfig.CONTAINS)],
                )
            ]
        ),
    )
    error = get_missing_index_error(error_for("exemption", Field.serialize(field)))
    assert error is not None
    assert error.declaration == (
        'collection_group_field_index("tags", order=False, array_contains=True)'
    )


def test_other_errors_are_raised_as_they_are() -> None:
    other = FailedPrecondition("The transaction has expired")
    unreadable = FailedPrecondition(
        "The query requires an index: https://console.firebase.google.com/?create_composite=%%%"
    )
    for error in (other, unreadable):
        assert get_missing_index_error(error) is None
        with pytest.raises(FailedPrecondition) as raised:
            with report_missing_index():
                raise error
        assert raised.value is error


def test_report_missing_index() -> None:
    data = composite(Index.QueryScope.COLLECTION, [Index.IndexField(field_path="a", order=ASC)])
    original = error_for("composite", data)
    with pytest.raises(MissingIndexError) as raised:
        with report_missing_index():
            raise original
    assert raised.value.__cause__ is original
