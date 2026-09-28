from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum, IntEnum
from unittest.mock import Mock
from uuid import UUID

from google.cloud.firestore_v1 import DELETE_FIELD, SERVER_TIMESTAMP, ArrayUnion, GeoPoint, Increment
from google.cloud.firestore_v1._helpers import encode_dict
from google.cloud.firestore_v1.document import DocumentReference
from google.cloud.firestore_v1.vector import Vector
from pydantic import BaseModel, HttpUrl, Secret, SecretBytes, SecretStr

from firedantic import to_firestore_value


class Fmt(Enum):
    VIRTUAL = "virtual"


class Level(IntEnum):
    HIGH = 3


class Venue(BaseModel):
    url: HttpUrl
    opened: date


def test_converted_values() -> None:
    assert to_firestore_value(HttpUrl("https://example.com")) == "https://example.com/"
    assert to_firestore_value(date(2026, 10, 1)) == "2026-10-01"
    assert to_firestore_value(Decimal("12.50")) == "12.50"
    assert to_firestore_value(Fmt.VIRTUAL) == "virtual"
    assert to_firestore_value(Level.HIGH) == 3
    assert type(to_firestore_value(Level.HIGH)) is int
    assert to_firestore_value(UUID(int=1)) == "00000000-0000-0000-0000-000000000001"
    assert to_firestore_value(timedelta(minutes=45)) == 2700.0
    assert to_firestore_value(Venue(url=HttpUrl("https://x.org"), opened=date(2020, 1, 2))) == {
        "url": "https://x.org/",
        "opened": "2020-01-02",
    }


def test_containers() -> None:
    value = {"a": [date(2026, 1, 1), {"b": (Fmt.VIRTUAL, Decimal("1"))}], "c": {Fmt.VIRTUAL}}
    assert to_firestore_value(value) == {"a": ["2026-01-01", {"b": ["virtual", "1"]}], "c": ["virtual"]}


def test_native_values_are_kept() -> None:
    now = datetime.now(timezone.utc)
    ref = DocumentReference("c", "d", client=Mock())
    point = GeoPoint(1.0, 2.0)
    vector = Vector([1.0, 2.0])
    increment = Increment(2)
    for value in (None, True, 3, 1.5, "s", b"b", now, ref, point, vector, increment, DELETE_FIELD, SERVER_TIMESTAMP):
        assert to_firestore_value(value) is value


def test_array_transforms_are_converted() -> None:
    union = to_firestore_value(ArrayUnion([date(2026, 1, 1), Fmt.VIRTUAL]))
    assert isinstance(union, ArrayUnion)
    assert union.values == ["2026-01-01", "virtual"]


def test_firestore_accepts_converted_values() -> None:
    value = {
        "url": HttpUrl("https://example.com"),
        "date": date(2026, 10, 1),
        "amount": Decimal("12.50"),
        "fmt": Fmt.VIRTUAL,
        "uid": UUID(int=1),
        "duration": timedelta(minutes=45),
        "venues": [Venue(url=HttpUrl("https://x.org"), opened=date(2020, 1, 2))],
    }
    encode_dict(to_firestore_value(value))


def test_secrets_are_stored_as_their_value() -> None:
    # pydantic's JSON form of a secret is "**********", which would lose the value
    assert to_firestore_value(SecretStr("hunter2")) == "hunter2"
    assert to_firestore_value(SecretBytes(b"k3y")) == b"k3y"
    assert to_firestore_value(Secret[date](date(2026, 1, 2))) == "2026-01-02"
    assert to_firestore_value({"nested": [SecretStr("a")]}) == {"nested": ["a"]}
