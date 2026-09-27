import logging
from typing import List, Literal, Optional

import pytest
from google.cloud.firestore import Query

from firedantic import (
    AsyncModel,
    AsyncSubCollection,
    AsyncSubModel,
    async_set_up_composite_indexes,
    collection_group_index,
)
from firedantic.common import IndexField
from firedantic.configurations import configuration
from firedantic.exceptions import ModelNotFoundError


class Animal(AsyncModel):
    __collection__ = "animals"
    name: str


class Site(AsyncModel):
    __collection__ = "sites"
    name: str


class AnimalSurvey(AsyncSubModel):
    __discriminator__ = "kind"
    __composite_indexes__ = [
        collection_group_index(
            IndexField("kind", Query.ASCENDING), IndexField("score", Query.DESCENDING)
        ),
    ]

    kind: Literal["animal_survey"] = "animal_survey"
    status: str = "open"
    score: int = 0

    class Collection(AsyncSubCollection):
        __collection_tpl__ = "animals/{id}/surveys"


class SiteSurvey(AsyncSubModel):
    __discriminator__ = "kind"

    kind: str = "site_survey"
    status: str = "open"
    score: int = 0

    class Collection(AsyncSubCollection):
        __collection_tpl__ = "sites/{id}/surveys"


class Survey(AsyncModel):
    """Top-level collection sharing the "surveys" name with the subcollections."""

    __collection__ = "surveys"

    kind: str = "animal_survey"
    status: str = "open"
    score: int = 0


class UndiscriminatedAnimalSurvey(AsyncSubModel):
    kind: str = "animal_survey"
    status: str = "open"
    score: int = 0

    class Collection(AsyncSubCollection):
        __collection_tpl__ = "animals/{id}/surveys"


async def _create_surveys() -> List[Animal]:
    animals = []
    for name in ("lion", "zebra"):
        animal = Animal(name=name)
        await animal.save()
        animals.append(animal)
        for score in range(3):
            await AnimalSurvey.model_for(animal)(score=score + 10 * len(animals)).save()

    site = Site(name="savanna")
    await site.save()
    await SiteSurvey.model_for(site)(score=100).save()
    await Survey(score=200).save()
    return animals


@pytest.mark.asyncio
async def test_find_in_group() -> None:
    animals = await _create_surveys()

    surveys = await AnimalSurvey.find_in_group(order_by=[("score", Query.ASCENDING)])

    assert [s.score for s in surveys] == [10, 11, 12, 20, 21, 22]
    assert all(type(s) is AnimalSurvey for s in surveys)
    assert [s.get_parent_id() for s in surveys] == [animals[0].id] * 3 + [animals[1].id] * 3
    prefix = configuration.get_config("(default)").prefix
    assert surveys[0].get_document_path() == f"{prefix}animals/{animals[0].id}/surveys/{surveys[0].id}"


@pytest.mark.asyncio
async def test_find_in_group_filter() -> None:
    await _create_surveys()

    surveys = await AnimalSurvey.find_in_group(
        {"score": {">=": 12}}, order_by=[("score", Query.DESCENDING)], limit=2
    )

    assert [s.score for s in surveys] == [22, 21]


@pytest.mark.asyncio
async def test_find_in_group_top_level_model() -> None:
    await _create_surveys()

    surveys = await Survey.find_in_group()

    assert [s.score for s in surveys] == [200]


@pytest.mark.asyncio
async def test_find_in_group_skips_mismatched_paths(caplog) -> None:
    await _create_surveys()
    # Nested deeper under animals/, with a matching kind, so only the path check
    # can exclude it
    animal = (await Animal.find())[0]
    nested = animal._get_doc_ref().collection("visits").document("v").collection("surveys")
    await nested.document("nested").set({"kind": "animal_survey", "score": 999})

    with caplog.at_level(logging.WARNING, logger="firedantic"):
        surveys = await AnimalSurvey.find_in_group()

    assert len(surveys) == 6
    assert 999 not in [s.score for s in surveys]
    assert "path does not match" in caplog.text


@pytest.mark.asyncio
async def test_find_in_group_fills_pages_after_skipping() -> None:
    await _create_surveys()
    # Nested documents sorting first, so the path check skips them from the first page
    animal = (await Animal.find())[0]
    nested = animal._get_doc_ref().collection("visits").document("v").collection("surveys")
    for score in range(5):
        await nested.document(f"nested-{score}").set({"kind": "animal_survey", "score": score})
    order_by = [("score", Query.ASCENDING)]

    page_1 = await AnimalSurvey.find_in_group(order_by=order_by, limit=4)
    page_2 = await AnimalSurvey.find_in_group(order_by=order_by, limit=4, start_after=page_1[-1])
    with_offset = await AnimalSurvey.find_in_group(order_by=order_by, limit=4, offset=6)

    assert [s.score for s in page_1] == [10, 11, 12, 20]
    assert [s.score for s in page_2] == [21, 22]
    # The offset counts the 5 skipped documents too
    assert [s.score for s in with_offset] == [11, 12, 20, 21]


@pytest.mark.asyncio
async def test_find_in_group_without_discriminator() -> None:
    await _create_surveys()
    site = Site(name="mislabeled")
    await site.save()
    await SiteSurvey.model_for(site)(kind="animal_survey", score=999).save()

    surveys = await UndiscriminatedAnimalSurvey.find_in_group()

    # Surveys under sites/ and the top-level surveys are outside animals/
    assert sorted(s.score for s in surveys) == [10, 11, 12, 20, 21, 22]



@pytest.mark.asyncio
async def test_count_in_group() -> None:
    assert await AnimalSurvey.count_in_group() == 0

    await _create_surveys()

    assert await AnimalSurvey.count_in_group() == 6
    assert await AnimalSurvey.count_in_group({"score": {">=": 12}}) == 4
    assert await AnimalSurvey.count_in_group({"status": "closed"}) == 0
    # Surveys under sites/ and the top-level surveys are outside animals/
    assert await UndiscriminatedAnimalSurvey.count_in_group() == 6
    assert await SiteSurvey.count_in_group() == 1
    assert await Survey.count_in_group() == 1


@pytest.mark.asyncio
async def test_count_in_group_includes_mismatched_paths() -> None:
    await _create_surveys()
    animal = (await Animal.find())[0]
    nested = animal._get_doc_ref().collection("visits").document("v").collection("surveys")
    await nested.document("nested").set({"kind": "animal_survey", "score": 999})
    await nested.document("other").set({"kind": "other", "score": 999})

    # Unlike find_in_group(), the count can't check paths, only the discriminator
    assert len(await AnimalSurvey.find_in_group()) == 6
    assert await AnimalSurvey.count_in_group() == 7

@pytest.mark.asyncio
async def test_find_in_group_pagination() -> None:
    await _create_surveys()
    order_by = [("score", Query.ASCENDING)]

    page_1 = await AnimalSurvey.find_in_group(order_by=order_by, limit=4)
    page_2 = await AnimalSurvey.find_in_group(order_by=order_by, limit=4, start_after=page_1[-1])
    # A document path works as a cursor too, e.g. when passed through an API
    page_2_by_path = await AnimalSurvey.find_in_group(
        order_by=order_by, limit=4, start_after=page_1[-1].get_document_path()
    )
    page_3 = await AnimalSurvey.find_in_group(order_by=order_by, limit=4, start_after=page_2[-1])

    assert [s.score for s in page_1] == [10, 11, 12, 20]
    assert [s.score for s in page_2] == [21, 22]
    assert [s.id for s in page_2_by_path] == [s.id for s in page_2]
    assert page_3 == []


@pytest.mark.asyncio
async def test_find_in_group_pagination_with_ties() -> None:
    animal = Animal(name="tie")
    await animal.save()
    for _ in range(5):
        await AnimalSurvey.model_for(animal)(score=1).save()

    seen: List[str] = []
    cursor: Optional[AnimalSurvey] = None
    while True:
        page = await AnimalSurvey.find_in_group(
            order_by=[("score", Query.ASCENDING)], limit=2, start_after=cursor
        )
        if not page:
            break
        seen.extend(s.id for s in page)  # type: ignore[misc]
        cursor = page[-1]

    assert len(seen) == 5
    assert len(set(seen)) == 5


@pytest.mark.asyncio
async def test_find_in_group_pagination_with_inequality_filter() -> None:
    await _create_surveys()

    page_1 = await AnimalSurvey.find_in_group({"score": {">": 10}}, limit=3)
    page_2 = await AnimalSurvey.find_in_group({"score": {">": 10}}, limit=3, start_after=page_1[-1])

    assert [s.score for s in page_1 + page_2] == [11, 12, 20, 21, 22]


@pytest.mark.asyncio
async def test_find_in_group_invalid_cursor() -> None:
    with pytest.raises(ModelNotFoundError):
        await AnimalSurvey.find_in_group(start_after="animals/nope/surveys/nope")


@pytest.mark.asyncio
async def test_find_one_in_group() -> None:
    with pytest.raises(ModelNotFoundError):
        await AnimalSurvey.find_one_in_group()

    await _create_surveys()

    survey = await AnimalSurvey.find_one_in_group({"score": {"==": 21}})

    assert survey.score == 21


@pytest.mark.asyncio
async def test_group_results_save_reload_delete() -> None:
    animals = await _create_surveys()
    animal_surveys = AnimalSurvey.model_for(animals[1])

    survey = await AnimalSurvey.find_one_in_group({"score": {"==": 21}})
    survey.status = "closed"
    await survey.save()
    assert (await animal_surveys.get_by_id(survey.id)).status == "closed"  # type: ignore[arg-type]

    other = await animal_surveys.get_by_id(survey.id)  # type: ignore[arg-type]
    other.score = 50
    await other.save()
    await survey.reload()
    assert survey.score == 50

    await survey.delete()
    with pytest.raises(ModelNotFoundError):
        await animal_surveys.get_by_id(survey.id)  # type: ignore[arg-type]
    assert len(await AnimalSurvey.find_in_group()) == 5


@pytest.mark.asyncio
async def test_get_parent_id_for_bound_model() -> None:
    animal = Animal(name="lion")
    await animal.save()
    survey = AnimalSurvey.model_for(animal)()
    await survey.save()

    assert survey.get_parent_id() == animal.id


def test_get_collection_group_id() -> None:
    prefix = configuration.get_config("(default)").prefix

    assert AnimalSurvey.get_collection_group_id() == "surveys"
    assert AnimalSurvey.model_for(Animal(id="x", name="x")).get_collection_group_id() == "surveys"
    assert Survey.get_collection_group_id() == f"{prefix}surveys"

    class Renamed(AsyncModel):
        __collection__ = "things"
        __collection_group__ = "stuff"

    assert Renamed.get_collection_group_id() == "stuff"


def test_invalid_discriminator() -> None:
    with pytest.raises(ValueError, match="must name a field with a default value"):

        class NoDefault(AsyncModel):
            __collection__ = "broken"
            __discriminator__ = "kind"
            kind: str

    with pytest.raises(ValueError, match="must name a field with a default value"):

        class NoField(AsyncSubModel):
            __discriminator__ = "kind"

            class Collection(AsyncSubCollection):
                __collection_tpl__ = "animals/{id}/broken"


@pytest.mark.asyncio
async def test_find_in_group_placeholder_root() -> None:
    class Parent(AsyncModel):
        __collection__ = "parents"
        collection: str = "herds"

    class HerdSurvey(AsyncSubModel):
        kind: str = "herd_survey"
        score: int = 0

        class Collection(AsyncSubCollection):
            __collection_tpl__ = "{collection}/{id}/surveys"

    parent = Parent()
    await parent.save()
    await HerdSurvey.model_for(parent)(score=5).save()

    surveys = await HerdSurvey.find_in_group({"kind": "herd_survey"})

    assert [s.get_parent_id() for s in surveys] == [parent.id]


@pytest.mark.asyncio
async def test_sub_model_composite_index_path(mock_admin_client) -> None:
    await async_set_up_composite_indexes(
        gcloud_project="proj", models=[AnimalSurvey], client=mock_admin_client
    )

    call_list = mock_admin_client.create_index.call_args_list
    assert len(call_list) == 1
    request = call_list[0][1]["request"]
    assert request.parent == "projects/proj/databases/(default)/collectionGroups/surveys"
    assert request.index.query_scope.name == "COLLECTION_GROUP"
