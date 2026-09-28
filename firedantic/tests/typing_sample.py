"""
Typed usage that must pass `mypy --strict`, checked by test_typing.py. It is only
type checked, never run.
"""

from typing import List, Optional

from typing_extensions import assert_type

from firedantic import (
    AsyncModel,
    AsyncSubCollection,
    AsyncSubModel,
    Model,
    SubCollection,
    SubModel,
)


class Event(AsyncModel):
    __collection__ = "events"
    name: str


class Talk(AsyncSubModel):
    title: str

    class Collection(AsyncSubCollection):
        __collection_tpl__ = "events/{id}/talks"


class Vote(AsyncSubModel):
    score: int

    class Collection(AsyncSubCollection):
        __collection_tpl__ = "events/{event_id}/talks/{id}/votes"


async def use_async(event: Event) -> None:
    talks = Talk.model_for(event)
    assert_type(talks, type[Talk])
    assert_type(Talk.Collection.model_for(event, Talk), type[Talk])
    assert_type(await talks.get_by_id("x"), Talk)
    assert_type(await talks.find({"title": "x"}), List[Talk])

    talk = talks(title="Typed")
    await talk.save()
    # The ID field is Optional, require_document_id() gives a str
    assert_type(talk.id, Optional[str])
    assert_type(await talks.get_by_id(talk.require_document_id()), Talk)
    # A sub-model can be the parent of another sub-model
    assert_type(Vote.model_for(talk)(score=5), Vote)

    # Wrong fields are caught; the ignore fails the check if this stops being an error
    talks(titel="Typo")  # type: ignore[call-arg]


class SyncEvent(Model):
    __collection__ = "events"
    name: str


class SyncTalk(SubModel):
    title: str

    class Collection(SubCollection):
        __collection_tpl__ = "events/{id}/talks"


def use_sync(event: SyncEvent) -> None:
    talks = SyncTalk.model_for(event)
    assert_type(talks, type[SyncTalk])
    assert_type(talks.get_by_id("x"), SyncTalk)
    talks(title="Typed").save()
    talks(titel="Typo")  # type: ignore[call-arg]
