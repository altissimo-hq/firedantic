"""Models for the index export tests, found by module name like an app's models."""

from datetime import datetime
from typing import List, Optional

from google.cloud.firestore import Query

from firedantic import (
    AsyncModel,
    AsyncSubCollection,
    AsyncSubModel,
    Model,
    collection_group_field_index,
    collection_group_index,
    collection_index,
)
from firedantic.common import IndexField


class Event(Model):
    __collection__ = "events"
    __ttl_field__ = "expire"
    __composite_indexes__ = [
        collection_index(
            IndexField("status", Query.ASCENDING), IndexField("start", Query.DESCENDING)
        ),
    ]
    status: str
    start: datetime
    expire: Optional[datetime] = None


class Participation(AsyncSubModel):
    __composite_indexes__ = [
        collection_group_index(
            IndexField("status", Query.ASCENDING), IndexField("score", Query.ASCENDING)
        ),
    ]
    __field_indexes__ = [
        collection_group_field_index("person_id"),
        collection_group_field_index("tags", order=False, array_contains=True),
    ]
    person_id: str
    status: str
    score: int
    tags: List[str] = []

    class Collection(AsyncSubCollection):
        __collection_tpl__ = "events/{id}/participations"


class Plain(AsyncModel):
    """Declares nothing, so it isn't exported."""

    __collection__ = "plain"
    name: str
