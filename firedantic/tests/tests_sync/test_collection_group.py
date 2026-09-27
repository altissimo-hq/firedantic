import logging
from typing import List, Literal, Optional

import pytest
from google.cloud.firestore import Query

from firedantic import (
    Model,
    SubCollection,
    SubModel,
    set_up_composite_indexes,
    collection_group_index,
)
from firedantic.common import IndexField
from firedantic.configurations import configuration
from firedantic.exceptions import ModelNotFoundError


class Animal(Model):
    __collection__ = "animals"
    name: str


class Site(Model):
    __collection__ = "sites"
    name: str


class AnimalSurvey(SubModel):
    __discriminator__ = "kind"
    __composite_indexes__ = [
        collection_group_index(
            IndexField("kind", Query.ASCENDING), IndexField("score", Query.DESCENDING)
        ),
    ]

    kind: Literal["animal_survey"] = "animal_survey"
    status: str = "open"
    score: int = 0

    class Collection(SubCollection):
        __collection_tpl__ = "animals/{id}/surveys"


class SiteSurvey(SubModel):
    __discriminator__ = "kind"

    kind: str = "site_survey"
    status: str = "open"
    score: int = 0

    class Collection(SubCollection):
        __collection_tpl__ = "sites/{id}/surveys"


class Survey(Model):
    """Top-level collection sharing the "surveys" name with the subcollections."""

    __collection__ = "surveys"

    kind: str = "animal_survey"
    status: str = "open"
    score: int = 0


class UndiscriminatedAnimalSurvey(SubModel):
    kind: str = "animal_survey"
    status: str = "open"
    score: int = 0

    class Collection(SubCollection):
        __collection_tpl__ = "animals/{id}/surveys"


def _create_surveys() -> List[Animal]:
    animals = []
    for name in ("lion", "zebra"):
        animal = Animal(name=name)
        animal.save()
        animals.append(animal)
        for score in range(3):
            AnimalSurvey.model_for(animal)(score=score + 10 * len(animals)).save()

    site = Site(name="savanna")
    site.save()
    SiteSurvey.model_for(site)(score=100).save()
    Survey(score=200).save()
    return animals



def test_find_in_group() -> None:
    animals = _create_surveys()

    surveys = AnimalSurvey.find_in_group(order_by=[("score", Query.ASCENDING)])

    assert [s.score for s in surveys] == [10, 11, 12, 20, 21, 22]
    assert all(type(s) is AnimalSurvey for s in surveys)
    assert [s.get_parent_id() for s in surveys] == [animals[0].id] * 3 + [animals[1].id] * 3
    prefix = configuration.get_config("(default)").prefix
    assert surveys[0].get_document_path() == f"{prefix}animals/{animals[0].id}/surveys/{surveys[0].id}"



def test_find_in_group_filter() -> None:
    _create_surveys()

    surveys = AnimalSurvey.find_in_group(
        {"score": {">=": 12}}, order_by=[("score", Query.DESCENDING)], limit=2
    )

    assert [s.score for s in surveys] == [22, 21]



def test_find_in_group_top_level_model() -> None:
    _create_surveys()

    surveys = Survey.find_in_group()

    assert [s.score for s in surveys] == [200]



def test_find_in_group_skips_mismatched_paths(caplog) -> None:
    _create_surveys()
    # Nested deeper under animals/, with a matching kind, so only the path check
    # can exclude it
    animal = (Animal.find())[0]
    nested = animal._get_doc_ref().collection("visits").document("v").collection("surveys")
    nested.document("nested").set({"kind": "animal_survey", "score": 999})

    with caplog.at_level(logging.WARNING, logger="firedantic"):
        surveys = AnimalSurvey.find_in_group()

    assert len(surveys) == 6
    assert 999 not in [s.score for s in surveys]
    assert "path does not match" in caplog.text



def test_find_in_group_fills_pages_after_skipping() -> None:
    _create_surveys()
    # Nested documents sorting first, so the path check skips them from the first page
    animal = (Animal.find())[0]
    nested = animal._get_doc_ref().collection("visits").document("v").collection("surveys")
    for score in range(5):
        nested.document(f"nested-{score}").set({"kind": "animal_survey", "score": score})
    order_by = [("score", Query.ASCENDING)]

    page_1 = AnimalSurvey.find_in_group(order_by=order_by, limit=4)
    page_2 = AnimalSurvey.find_in_group(order_by=order_by, limit=4, start_after=page_1[-1])
    with_offset = AnimalSurvey.find_in_group(order_by=order_by, limit=4, offset=6)

    assert [s.score for s in page_1] == [10, 11, 12, 20]
    assert [s.score for s in page_2] == [21, 22]
    # The offset counts the 5 skipped documents too
    assert [s.score for s in with_offset] == [11, 12, 20, 21]



def test_find_in_group_without_discriminator() -> None:
    _create_surveys()
    site = Site(name="mislabeled")
    site.save()
    SiteSurvey.model_for(site)(kind="animal_survey", score=999).save()

    surveys = UndiscriminatedAnimalSurvey.find_in_group()

    # Surveys under sites/ and the top-level surveys are outside animals/
    assert sorted(s.score for s in surveys) == [10, 11, 12, 20, 21, 22]



def test_find_in_group_pagination() -> None:
    _create_surveys()
    order_by = [("score", Query.ASCENDING)]

    page_1 = AnimalSurvey.find_in_group(order_by=order_by, limit=4)
    page_2 = AnimalSurvey.find_in_group(order_by=order_by, limit=4, start_after=page_1[-1])
    # A document path works as a cursor too, e.g. when passed through an API
    page_2_by_path = AnimalSurvey.find_in_group(
        order_by=order_by, limit=4, start_after=page_1[-1].get_document_path()
    )
    page_3 = AnimalSurvey.find_in_group(order_by=order_by, limit=4, start_after=page_2[-1])

    assert [s.score for s in page_1] == [10, 11, 12, 20]
    assert [s.score for s in page_2] == [21, 22]
    assert [s.id for s in page_2_by_path] == [s.id for s in page_2]
    assert page_3 == []



def test_find_in_group_pagination_with_ties() -> None:
    animal = Animal(name="tie")
    animal.save()
    for _ in range(5):
        AnimalSurvey.model_for(animal)(score=1).save()

    seen: List[str] = []
    cursor: Optional[AnimalSurvey] = None
    while True:
        page = AnimalSurvey.find_in_group(
            order_by=[("score", Query.ASCENDING)], limit=2, start_after=cursor
        )
        if not page:
            break
        seen.extend(s.id for s in page)  # type: ignore[misc]
        cursor = page[-1]

    assert len(seen) == 5
    assert len(set(seen)) == 5



def test_find_in_group_pagination_with_inequality_filter() -> None:
    _create_surveys()

    page_1 = AnimalSurvey.find_in_group({"score": {">": 10}}, limit=3)
    page_2 = AnimalSurvey.find_in_group({"score": {">": 10}}, limit=3, start_after=page_1[-1])

    assert [s.score for s in page_1 + page_2] == [11, 12, 20, 21, 22]



def test_find_in_group_invalid_cursor() -> None:
    with pytest.raises(ModelNotFoundError):
        AnimalSurvey.find_in_group(start_after="animals/nope/surveys/nope")



def test_find_one_in_group() -> None:
    with pytest.raises(ModelNotFoundError):
        AnimalSurvey.find_one_in_group()

    _create_surveys()

    survey = AnimalSurvey.find_one_in_group({"score": {"==": 21}})

    assert survey.score == 21



def test_group_results_save_reload_delete() -> None:
    animals = _create_surveys()
    animal_surveys = AnimalSurvey.model_for(animals[1])

    survey = AnimalSurvey.find_one_in_group({"score": {"==": 21}})
    survey.status = "closed"
    survey.save()
    assert (animal_surveys.get_by_id(survey.id)).status == "closed"  # type: ignore[arg-type]

    other = animal_surveys.get_by_id(survey.id)  # type: ignore[arg-type]
    other.score = 50
    other.save()
    survey.reload()
    assert survey.score == 50

    survey.delete()
    with pytest.raises(ModelNotFoundError):
        animal_surveys.get_by_id(survey.id)  # type: ignore[arg-type]
    assert len(AnimalSurvey.find_in_group()) == 5



def test_get_parent_id_for_bound_model() -> None:
    animal = Animal(name="lion")
    animal.save()
    survey = AnimalSurvey.model_for(animal)()
    survey.save()

    assert survey.get_parent_id() == animal.id


def test_get_collection_group_id() -> None:
    prefix = configuration.get_config("(default)").prefix

    assert AnimalSurvey.get_collection_group_id() == "surveys"
    assert AnimalSurvey.model_for(Animal(id="x", name="x")).get_collection_group_id() == "surveys"
    assert Survey.get_collection_group_id() == f"{prefix}surveys"

    class Renamed(Model):
        __collection__ = "things"
        __collection_group__ = "stuff"

    assert Renamed.get_collection_group_id() == "stuff"


def test_invalid_discriminator() -> None:
    with pytest.raises(ValueError, match="must name a field with a default value"):

        class NoDefault(Model):
            __collection__ = "broken"
            __discriminator__ = "kind"
            kind: str

    with pytest.raises(ValueError, match="must name a field with a default value"):

        class NoField(SubModel):
            __discriminator__ = "kind"

            class Collection(SubCollection):
                __collection_tpl__ = "animals/{id}/broken"



def test_find_in_group_placeholder_root() -> None:
    class Parent(Model):
        __collection__ = "parents"
        collection: str = "herds"

    class HerdSurvey(SubModel):
        kind: str = "herd_survey"
        score: int = 0

        class Collection(SubCollection):
            __collection_tpl__ = "{collection}/{id}/surveys"

    parent = Parent()
    parent.save()
    HerdSurvey.model_for(parent)(score=5).save()

    surveys = HerdSurvey.find_in_group({"kind": "herd_survey"})

    assert [s.get_parent_id() for s in surveys] == [parent.id]



def test_sub_model_composite_index_path(mock_admin_client) -> None:
    set_up_composite_indexes(
        gcloud_project="proj", models=[AnimalSurvey], client=mock_admin_client
    )

    call_list = mock_admin_client.create_index.call_args_list
    assert len(call_list) == 1
    request = call_list[0][1]["request"]
    assert request.parent == "projects/proj/databases/(default)/collectionGroups/surveys"
    assert request.index.query_scope.name == "COLLECTION_GROUP"
