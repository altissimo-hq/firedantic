import json
from typing import Any, Dict

import pytest
from google.cloud.firestore import Query

from firedantic import AsyncModel, collection_index
from firedantic.__main__ import main
from firedantic.common import IndexField
from firedantic.configurations import configuration
from firedantic.index_export import (
    export_firestore_indexes,
    find_models,
    merge_firestore_indexes,
)
from firedantic.tests.index_export_models import Event, Participation, Plain

AUTOMATIC = [
    {"order": "ASCENDING", "queryScope": "COLLECTION"},
    {"order": "DESCENDING", "queryScope": "COLLECTION"},
    {"arrayConfig": "CONTAINS", "queryScope": "COLLECTION"},
]
EXPECTED: Dict[str, Any] = {
    "indexes": [
        {
            "collectionGroup": "participations",
            "queryScope": "COLLECTION_GROUP",
            "fields": [
                {"fieldPath": "status", "order": "ASCENDING"},
                {"fieldPath": "score", "order": "ASCENDING"},
            ],
        },
        {
            "collectionGroup": "test_events",
            "queryScope": "COLLECTION",
            "fields": [
                {"fieldPath": "status", "order": "ASCENDING"},
                {"fieldPath": "start", "order": "DESCENDING"},
            ],
        },
    ],
    "fieldOverrides": [
        {
            "collectionGroup": "participations",
            "fieldPath": "person_id",
            "indexes": AUTOMATIC
            + [
                {"order": "ASCENDING", "queryScope": "COLLECTION_GROUP"},
                {"order": "DESCENDING", "queryScope": "COLLECTION_GROUP"},
            ],
        },
        {
            "collectionGroup": "participations",
            "fieldPath": "tags",
            "indexes": AUTOMATIC + [{"arrayConfig": "CONTAINS", "queryScope": "COLLECTION_GROUP"}],
        },
        {
            "collectionGroup": "test_events",
            "fieldPath": "expire",
            "indexes": AUTOMATIC,
            "ttl": True,
        },
    ],
}
MODULE = "firedantic.tests.index_export_models"


@pytest.fixture(autouse=True)
def configure() -> None:
    configuration.add(name="(default)", project="proj", prefix="test_")


def test_export() -> None:
    assert export_firestore_indexes([Plain, Participation, Event]) == EXPECTED
    # The same declarations from several models are exported once
    assert export_firestore_indexes([Event, Participation, Event, Participation]) == EXPECTED


def test_find_models() -> None:
    assert find_models([MODULE]) == [Event, Participation, Plain]
    # Plain (name, order) tuples work as index fields too
    original = Event.__composite_indexes__
    Event.__composite_indexes__ = [
        collection_index(("status", "ASCENDING"), ("start", "DESCENDING"))  # type: ignore[arg-type]
    ]
    try:
        assert export_firestore_indexes([Event])["indexes"] == [EXPECTED["indexes"][1]]
    finally:
        Event.__composite_indexes__ = original
    assert find_models(["firedantic.tests"]) != []


def test_merge_keeps_existing_entries() -> None:
    other_index = {
        "collectionGroup": "other",
        "queryScope": "COLLECTION",
        "fields": [{"fieldPath": "a", "order": "ASCENDING"}],
    }
    existing: Dict[str, Any] = {
        "indexes": [
            other_index,
            # Already there, with the implied __name__ listed
            {
                "collectionGroup": "test_events",
                "queryScope": "COLLECTION",
                "fields": [
                    {"fieldPath": "status", "order": "ASCENDING"},
                    {"fieldPath": "start", "order": "DESCENDING"},
                    {"fieldPath": "__name__", "order": "DESCENDING"},
                ],
            },
        ],
        "fieldOverrides": [
            # An override that disables the automatic indexes on purpose
            {"collectionGroup": "participations", "fieldPath": "person_id", "indexes": []},
        ],
    }

    merged, changes = merge_firestore_indexes(existing, [Event, Participation])

    assert other_index in merged["indexes"]
    assert len(merged["indexes"]) == 3
    person_id = merged["fieldOverrides"][0]
    assert person_id["indexes"] == [
        {"order": "ASCENDING", "queryScope": "COLLECTION_GROUP"},
        {"order": "DESCENDING", "queryScope": "COLLECTION_GROUP"},
    ]
    assert changes == [
        "field override for test_events.expire",
        "COLLECTION_GROUP index on participations (status ASCENDING, score ASCENDING)",
        "COLLECTION_GROUP ASCENDING index in field override for participations.person_id",
        "COLLECTION_GROUP DESCENDING index in field override for participations.person_id",
        "field override for participations.tags",
    ]
    assert existing["fieldOverrides"][0]["indexes"] == []  # not changed in place

    # Merging again changes nothing
    assert merge_firestore_indexes(merged, [Event, Participation]) == (merged, [])


def test_databases() -> None:
    configuration.add(name="other", project="proj", database="other-db")

    class OtherEvent(AsyncModel):
        __db_config__ = "other"
        __collection__ = "other_events"
        __composite_indexes__ = [
            collection_index(IndexField("a", Query.ASCENDING), IndexField("b", Query.ASCENDING))
        ]

    with pytest.raises(ValueError):
        export_firestore_indexes([Event, OtherEvent])
    assert export_firestore_indexes([Event, OtherEvent], "(default)")["indexes"] == [
        EXPECTED["indexes"][1]
    ]
    exported = export_firestore_indexes([Event, OtherEvent], "other-db")
    assert [i["collectionGroup"] for i in exported["indexes"]] == ["other_events"]


def test_cli(tmp_path, capsys) -> None:
    assert main(["export-indexes", MODULE]) == 0
    assert json.loads(capsys.readouterr().out) == EXPECTED

    path = tmp_path / "firestore.indexes.json"
    assert main(["export-indexes", MODULE, "--check", str(path)]) == 1
    assert "is missing declared indexes" in capsys.readouterr().err

    assert main(["export-indexes", MODULE, "--update", str(path)]) == 0
    assert json.loads(path.read_text()) == EXPECTED
    assert "Updated" in capsys.readouterr().out
    assert main(["export-indexes", MODULE, "--check", str(path)]) == 0
