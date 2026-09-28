import base64
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from operator import attrgetter
from typing import Dict, List, Optional
from unittest.mock import Mock, Mock
from uuid import UUID, uuid4

import pytest
from google.api_core.exceptions import AlreadyExists, FailedPrecondition, NotFound
from google.cloud.firestore import Query, transactional
from google.cloud.firestore_admin_v1.types import Index
from google.cloud.firestore_v1 import DELETE_FIELD, ArrayUnion, Increment
from google.cloud.firestore_v1.transaction import Transaction
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError, field_serializer

import firedantic.operators as op
from firedantic import (
    Aggregates,
    Model,
    SubCollection,
    SubModel,
    get_batch,
    get_transaction,
)
from firedantic.configurations import configuration
from firedantic.exceptions import (
    CollectionNotDefined,
    InvalidDocumentID,
    MissingIndexError,
    ModelNotFoundError,
)
from firedantic.tests.tests_sync.conftest import (
    City,
    Company,
    CustomIDConflictModel,
    CustomIDModel,
    CustomIDModelExtra,
    Owner,
    Product,
    Profile,
    TodoList,
    User,
    UserStats,
    get_user_purchases,
)

TEST_PRODUCTS = [
    {"product_id": "a", "stock": 0},
    {"product_id": "b", "stock": 1},
    {"product_id": "c", "stock": 2},
    {"product_id": "d", "stock": 3},
]



def test_save_model(create_company) -> None:
    company = create_company()

    assert company.id is not None
    assert company.owner.first_name == "John"
    assert company.owner.last_name == "Doe"



def test_find_one(create_company) -> None:
    with pytest.raises(ModelNotFoundError):
        Company.find_one()

    company_a: Company = create_company(company_id="1234555-1", first_name="Foo")
    company_b: Company = create_company(company_id="1231231-2", first_name="Bar")

    a: Company = Company.find_one({"company_id": company_a.company_id})
    b: Company = Company.find_one({"company_id": company_b.company_id})

    assert a.company_id == company_a.company_id
    assert b.company_id == company_b.company_id
    assert a.owner.first_name == "Foo"
    assert b.owner.first_name == "Bar"

    with pytest.raises(ModelNotFoundError):
        Company.find_one({"company_id": "Foo"})

    random_company = Company.find_one()
    assert random_company.company_id in {a.company_id, b.company_id}

    first_asc = Company.find_one(order_by=[("owner.first_name", Query.ASCENDING)])
    assert first_asc.owner.first_name == "Bar"

    first_desc = Company.find_one(order_by=[("owner.first_name", Query.DESCENDING)])
    assert first_desc.owner.first_name == "Foo"



def test_find(create_company, create_product) -> None:
    ids = ["1234555-1", "1234567-8", "2131232-4", "4124432-4"]
    for company_id in ids:
        create_company(company_id=company_id)

    c = Company.find({"company_id": "4124432-4"})
    assert c[0].company_id == "4124432-4"
    assert c[0].owner.first_name == "John"

    d = Company.find({"owner.first_name": "John"})
    assert len(d) == 4

    d = Company.find({"owner.first_name": {op.EQ: "John"}})
    assert len(d) == 4

    d = Company.find({"owner.first_name": {"==": "John"}})
    assert len(d) == 4

    for p in TEST_PRODUCTS:
        create_product(**p)

    assert len(Product.find({})) == 4

    products = Product.find({"stock": {op.GTE: 1}})
    assert len(products) == 3

    products = Product.find({"stock": {op.GTE: 2, op.LT: 4}})
    assert len(products) == 2

    products = Product.find({"product_id": {op.IN: ["a", "d", "g"]}})
    assert len(products) == 2

    with pytest.raises(ValueError):
        Product.find({"product_id": {"<>": "a"}})




def test_count(create_product) -> None:
    assert Product.count() == 0

    for p in TEST_PRODUCTS:
        create_product(**p)

    assert Product.count() == 4
    assert Product.count({"stock": {op.GTE: 1}}) == 3
    assert Product.count({"stock": {op.GTE: 2, op.LT: 4}}) == 2
    assert Product.count({"product_id": "missing"}) == 0



def test_sum_and_avg(create_product) -> None:
    assert Product.sum("stock") == 0
    assert Product.avg("stock") is None

    for p in TEST_PRODUCTS:
        create_product(**p, price=1.5)

    assert Product.sum("stock") == 6
    assert Product.sum("stock", {"stock": {op.GTE: 2}}) == 5
    assert Product.sum("price") == 6.0
    assert Product.avg("stock") == 1.5
    assert Product.avg("stock", {"stock": {op.GTE: 2}}) == 2.5
    assert Product.avg("stock", {"product_id": "missing"}) is None



def test_sum_and_avg_paths() -> None:
    Counter(totalCount=1, stats=CounterStats(visits=2)).save()
    Counter(totalCount=3, stats=CounterStats(visits=4)).save()
    # Values that aren't numbers are ignored
    Counter(id="no-count").save()
    Counter(id="no-count", optional=1).save(exclude_unset=True)

    assert Counter.sum("totalCount") == 4
    assert Counter.sum("stats.visits") == 6
    assert Counter.avg("totalCount") == 2.0
    assert Counter.avg("optional") == 1.0
    assert Counter.avg("missing") is None



def test_aggregate(create_product) -> None:
    assert Product.aggregate(sum=["stock"], avg=["price"]) == Aggregates(
        count=0, sum={"stock": 0}, avg={"price": None}
    )

    for p in TEST_PRODUCTS:
        create_product(**p, price=2.0)

    result = Product.aggregate({"stock": {op.GTE: 1}}, sum=["stock", "price"], avg=["stock"])
    assert result.count == 3
    assert result.sum == {"stock": 6, "price": 6.0}
    assert result.avg == {"stock": 2.0}
    assert (Product.aggregate()).count == 4



def test_aggregate_only_includes_documents_with_every_field() -> None:
    Counter(totalCount=1, optional=10).save()
    Counter(totalCount=3).save(exclude_none=True)

    # The second counter has no "optional" field, so it's left out of everything
    result = Counter.aggregate(sum=["totalCount"], avg=["optional"])
    assert result == Aggregates(count=1, sum={"totalCount": 1}, avg={"optional": 10.0})
    assert Counter.count() == 2

    empty = Counter.aggregate(sum=["totalCount"], avg=["missing"])
    assert empty.count == 0
    assert empty.avg == {"missing": None}



def test_aggregate_limits() -> None:
    with pytest.raises(ValueError):
        Product.aggregate(sum=["a", "b", "c"], avg=["d", "e"])



def test_aggregate_in_transaction(create_product) -> None:
    create_product(stock=2)
    create_product(stock=4)

    @transactional
    def read_in_transaction(transaction: Transaction):
        return Product.aggregate(avg=["stock"], transaction=transaction)

    assert (read_in_transaction(get_transaction())).avg == {"stock": 3.0}



def test_sum_and_avg_in_transaction(create_product) -> None:
    create_product(stock=2)
    create_product(stock=4)

    @transactional
    def read_in_transaction(transaction: Transaction):
        return (
            Product.sum("stock", transaction=transaction),
            Product.avg("stock", transaction=transaction),
        )

    assert read_in_transaction(get_transaction()) == (6, 3.0)



def test_find_pagination(create_product) -> None:
    for p in TEST_PRODUCTS:
        create_product(**p)
    order_by = [("stock", Query.ASCENDING)]

    page_1 = Product.find(order_by=order_by, limit=2)
    page_2 = Product.find(order_by=order_by, limit=2, start_after=page_1[-1])
    # The document ID or path works as a cursor too, e.g. when passed through an API
    page_2_by_id = Product.find(order_by=order_by, limit=2, start_after=page_1[-1].id)
    page_2_by_path = Product.find(
        order_by=order_by, limit=2, start_after=page_1[-1].get_document_path()
    )
    page_3 = Product.find(order_by=order_by, limit=2, start_after=page_2[-1])

    assert [p.product_id for p in page_1] == ["a", "b"]
    assert [p.product_id for p in page_2] == ["c", "d"]
    assert page_2_by_id == page_2
    assert page_2_by_path == page_2
    assert page_3 == []



def test_find_pagination_with_filter_and_ties(create_product) -> None:
    for _ in range(5):
        create_product(stock=1)
    create_product(stock=0)

    # Without order_by the pages are ordered by the inequality field and document ID
    found = []
    page = Product.find({"stock": {op.GTE: 1}}, limit=2)
    while page:
        found.extend(page)
        page = Product.find({"stock": {op.GTE: 1}}, limit=2, start_after=page[-1])

    assert len(found) == 5
    assert len({p.id for p in found}) == 5



def test_find_backward_pagination(create_product) -> None:
    for p in TEST_PRODUCTS:
        create_product(**p)
    order_by = [("stock", Query.ASCENDING)]

    def ids(**kwargs) -> List[str]:
        return [p.product_id for p in Product.find(order_by=order_by, **kwargs)]

    page_2 = Product.find(order_by=order_by, offset=2, limit=2)
    assert [p.product_id for p in page_2] == ["c", "d"]
    # The page before page 2, by model, ID or path
    assert ids(end_before=page_2[0], limit_to_last=2) == ["a", "b"]
    assert ids(end_before=page_2[0].id, limit_to_last=2) == ["a", "b"]
    assert ids(end_before=page_2[0].get_document_path(), limit_to_last=1) == ["b"]
    assert ids(limit_to_last=3) == ["b", "c", "d"]
    assert ids(start_at=page_2[0]) == ["c", "d"]
    assert ids(end_at=page_2[0]) == ["a", "b", "c"]
    assert ids(start_after=page_2[0], end_at=page_2[1]) == ["d"]
    assert ids(start_at=page_2[0], end_before=page_2[1], limit_to_last=5) == ["c"]

    descending = [p.product_id for p in Product.find(order_by=[("stock", Query.DESCENDING)], limit_to_last=2)]
    assert descending == ["b", "a"]



def test_find_backward_pagination_with_filter_and_ties(create_product) -> None:
    for _ in range(5):
        create_product(stock=1)
    create_product(stock=0)
    filter_ = {"stock": {op.GTE: 1}}

    forward = Product.find(filter_)
    backward: List[Product] = []
    page = Product.find(filter_, limit_to_last=2)
    while page:
        backward = page + backward
        page = Product.find(filter_, limit_to_last=2, end_before=page[0])

    # Without order_by, both follow the inequality field, then the document ID
    assert [p.id for p in backward] == [p.id for p in forward]
    assert len(backward) == 5



def test_find_cursor_errors() -> None:
    order_by = [("stock", Query.ASCENDING)]
    for kwargs in (
        {"limit": 1, "limit_to_last": 1},
        {"offset": 1, "limit_to_last": 1},
        {"start_at": "a", "start_after": "b"},
        {"end_at": "a", "end_before": "b"},
    ):
        with pytest.raises(ValueError):
            Product.find(order_by=order_by, **kwargs)  # type: ignore[arg-type]



def test_find_pagination_missing_cursor() -> None:
    with pytest.raises(ModelNotFoundError):
        Product.find(start_after="missing")



def test_find_or(create_product) -> None:
    for p in TEST_PRODUCTS:
        create_product(**p)

    def find_ids(filter_: Dict) -> List[str]:
        return sorted(p.product_id for p in Product.find(filter_))

    assert find_ids({op.OR: [{"stock": 0}, {"stock": {op.GTE: 3}}]}) == ["a", "d"]
    # Top-level keys and the keys of each clause are combined with AND
    assert find_ids(
        {"product_id": {op.IN: ["a", "b", "c"]}, op.OR: [{"stock": 0}, {"stock": 3}]}
    ) == ["a"]
    assert find_ids({op.OR: [{"stock": {op.GTE: 1}, "product_id": "b"}, {"product_id": "a"}]}) == [
        "a",
        "b",
    ]
    # Nested AND and OR
    assert find_ids(
        {
            op.OR: [
                {op.AND: [{"stock": {op.GTE: 1}}, {"stock": {op.LT: 2}}]},
                {op.OR: [{"product_id": "d"}]},
            ]
        }
    ) == ["b", "d"]

    assert Product.count({op.OR: [{"stock": 0}, {"stock": 3}]}) == 2
    assert Product.sum("stock", {op.OR: [{"stock": 1}, {"stock": 3}]}) == 4
    found = Product.find_one({op.OR: [{"product_id": "c"}, {"product_id": "missing"}]})
    assert found.product_id == "c"



def test_find_or_invalid() -> None:
    filters: List[Dict] = [
        {op.OR: []},
        {op.OR: {"stock": 1}},
        {op.OR: [{}]},
        {op.AND: ["stock"]},
        {op.OR: [{"stock": {"~": 1}}]},
    ]
    for filter_ in filters:
        with pytest.raises(ValueError):
            Product.find(filter_)



def test_missing_index_is_reported(monkeypatch) -> None:
    index = Index(
        name="projects/p/databases/(default)/collectionGroups/products/indexes/_",
        query_scope=Index.QueryScope.COLLECTION,
        fields=[Index.IndexField(field_path="stock", order=Index.IndexField.Order.ASCENDING)],
    )
    encoded = base64.b64encode(Index.serialize(index)).decode()
    error = FailedPrecondition(
        f"The query requires an index. You can create it here: https://x/?create_composite={encoded}"
    )

    def stream(*args, **kwargs):
        raise error
        yield  # pragma: no cover

    query = Mock()
    query.stream = stream
    query.order_by.return_value = query
    query.limit.return_value = query
    query.count.return_value.get = Mock(side_effect=error)
    monkeypatch.setattr(Product, "_get_query", classmethod(lambda cls, filter_: query))

    with pytest.raises(MissingIndexError) as raised:
        Product.find({"stock": 1})
    assert raised.value.index_json["collectionGroup"] == "products"
    with pytest.raises(MissingIndexError):
        Product.count()


class Format(Enum):
    VIRTUAL = "virtual"
    IN_PERSON = "in_person"


class Venue(BaseModel):
    url: HttpUrl
    opened: date


class Event(Model):
    __collection__ = "events"

    url: HttpUrl
    day: date
    budget: Decimal
    format: Format
    uid: UUID
    duration: timedelta
    venue: Venue
    days: List[date] = []
    lines: Dict[str, Decimal] = {}


def make_event(**changes) -> Event:
    data = {
        "url": "https://example.com/event",
        "day": date(2026, 10, 1),
        "budget": Decimal("12.50"),
        "format": Format.VIRTUAL,
        "uid": UUID(int=1),
        "duration": timedelta(minutes=45),
        "venue": Venue(url=HttpUrl("https://example.com/venue"), opened=date(2020, 1, 2)),
        "days": [date(2026, 10, 1), date(2026, 10, 2)],
        "lines": {"food": Decimal("7.25")},
    }
    return Event(**{**data, **changes})



def test_save_pydantic_types() -> None:
    event = make_event()
    event.save()
    assert event.id

    assert get_stored_data(event) == {
        "url": "https://example.com/event",
        "day": "2026-10-01",
        "budget": "12.50",
        "format": "virtual",
        "uid": "00000000-0000-0000-0000-000000000001",
        "duration": 2700.0,
        "venue": {"url": "https://example.com/venue", "opened": "2020-01-02"},
        "days": ["2026-10-01", "2026-10-02"],
        "lines": {"food": "7.25"},
    }
    assert Event.get_by_id(event.id) == event

    created = make_event()
    created.create()
    batch = get_batch()
    batched = make_event()
    batched.save(batch=batch)
    batch.commit()
    assert batched.id and created.id
    assert Event.get_by_ids([created.id, batched.id]) == [created, batched]



def test_update_pydantic_types() -> None:
    event = make_event()
    event.save()
    assert event.id

    event.day = date(2026, 11, 5)
    event.update("day")
    event.update({"budget": Decimal("3.10"), "venue.opened": date(2021, 5, 6)})
    event.update({"days": ArrayUnion([date(2026, 12, 24)]), "format": Format.IN_PERSON})

    stored = get_stored_data(event)
    assert stored is not None
    assert stored["day"] == "2026-11-05"
    assert stored["budget"] == "3.10"
    assert stored["venue"]["opened"] == "2021-05-06"
    assert stored["days"] == ["2026-10-01", "2026-10-02", "2026-12-24"]
    assert stored["format"] == "in_person"
    assert event.venue.opened == date(2021, 5, 6)
    event.reload()
    assert event.days[-1] == date(2026, 12, 24)



def test_filter_pydantic_types() -> None:
    early = make_event(day=date(2026, 9, 1), duration=timedelta(minutes=30))
    late = make_event(day=date(2026, 10, 1), format=Format.IN_PERSON)
    early.save()
    late.save()

    def find_days(filter_: Dict) -> List[date]:
        return sorted(e.day for e in Event.find(filter_))

    assert find_days({"day": {op.GTE: date(2026, 9, 15)}}) == [late.day]
    assert find_days({"format": Format.VIRTUAL}) == [early.day]
    assert find_days({"format": {op.IN: [Format.IN_PERSON]}}) == [late.day]
    assert find_days({"duration": {op.GT: timedelta(minutes=40)}}) == [late.day]
    assert find_days({"days": {op.ARRAY_CONTAINS: date(2026, 10, 2)}}) == [early.day, late.day]
    assert find_days({"budget": Decimal("12.50")}) == [early.day, late.day]
    assert find_days({op.OR: [{"format": Format.IN_PERSON}, {"uid": UUID(int=2)}]}) == [late.day]
    assert Event.count({"day": {op.LT: date(2026, 9, 15)}}) == 1


class TimestampEvent(Model):
    __collection__ = "timestamp_events"
    day: date

    @field_serializer("day")
    def serialize_day(self, day: date) -> datetime:
        return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)



def test_field_serializer_takes_priority() -> None:
    event = TimestampEvent(day=date(2026, 10, 1))
    event.save()
    stored = get_stored_data(event)
    assert stored is not None
    assert stored["day"] == datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert (TimestampEvent.get_by_id(event.id)).day == date(2026, 10, 1)  # type: ignore[arg-type]



def test_get_by_ids(create_company) -> None:
    company_a = create_company(company_id="1234555-1")
    company_b = create_company(company_id="1231231-2")

    found = Company.get_by_ids([company_b.id, "missing", company_a.id, company_b.id])
    assert found == [company_b, company_a]

    assert Company.get_by_ids([]) == []
    with pytest.raises(ModelNotFoundError):
        Company.get_by_ids([company_a.id, "a/b"])



def test_submodel_get_by_ids() -> None:
    u = User(name="Foo")
    u.save()
    us = UserStats.model_for(u)
    us(id="2021", purchases=1).save()
    us(id="2022", purchases=2).save()

    found = us.get_by_ids(["2022", "2020", "2021"])
    assert [(s.id, s.purchases) for s in found] == [("2022", 2), ("2021", 1)]



def test_count_and_get_by_ids_in_transaction(create_company) -> None:
    company = create_company()

    @transactional
    def read_in_transaction(transaction: Transaction):
        return (
            Company.count(transaction=transaction),
            Company.get_by_ids([company.id], transaction=transaction),
        )

    assert read_in_transaction(get_transaction()) == (1, [company])


class CounterStats(BaseModel):
    visits: int = 0


class Counter(Model):
    __collection__ = "counters"
    model_config = ConfigDict(populate_by_name=True)

    total: int = Field(default=0, alias="totalCount")
    stats: CounterStats = CounterStats()
    by_day: Dict[str, int] = {}
    optional: Optional[int] = None



def test_increment(create_product) -> None:
    product = create_product(stock=3)
    assert product.id

    product.increment("stock", 5)
    assert product.stock == 8
    assert (Product.get_by_id(product.id)).stock == 8

    product.increment("stock", -3)
    product.reload()
    assert product.stock == 5



def test_increment_paths() -> None:
    counter = Counter(by_day={"mon": 1})
    counter.save(exclude_none=True)
    assert counter.id

    # Aliased, nested, dict and missing fields are updated the way Firestore does
    counter.increment("totalCount")
    counter.increment("stats.visits", 2)
    counter.increment("by_day.mon", 3)
    counter.increment("by_day.tue", 5)
    counter.increment("optional", 4)

    assert counter.total == 1
    assert counter.stats.visits == 2
    assert counter.by_day == {"mon": 4, "tue": 5}
    assert counter.optional == 4
    assert counter == Counter.get_by_id(counter.id)



def test_increment_unsaved() -> None:
    with pytest.raises(ModelNotFoundError):
        Counter().increment("totalCount")



def test_increment_in_transaction() -> None:
    counter = Counter()
    counter.save()

    @transactional
    def increment_in_transaction(transaction: Transaction) -> None:
        counter.increment("totalCount", 2, transaction=transaction)

    increment_in_transaction(get_transaction())

    # The write happens on commit, so the instance is not changed
    assert counter.total == 0
    counter.reload()
    assert counter.total == 2

def get_stored_data(model: Model) -> Optional[Dict]:
    # pylint: disable=protected-access
    return (model._get_doc_ref().get()).to_dict()



def test_save_merge() -> None:
    p = Profile(name="Foo", photo_url="old")
    p.save()
    assert p.id

    # Only the set fields are written, the stored name is kept
    Profile(id=p.id, photo_url="new").save(exclude_unset=True, merge=True)
    assert get_stored_data(p) == {"name": "Foo", "photo_url": "new"}

    # Without merge the document is replaced
    Profile(id=p.id, photo_url="newer").save(exclude_unset=True)
    assert get_stored_data(p) == {"photo_url": "newer"}



def test_save_merge_in_transaction() -> None:
    p = Profile(name="Foo", photo_url="old")
    p.save()

    @transactional
    def save_in_transaction(transaction: Transaction) -> None:
        Profile(id=p.id, photo_url="new").save(
            exclude_unset=True, merge=True, transaction=transaction
        )

    save_in_transaction(get_transaction())
    assert get_stored_data(p) == {"name": "Foo", "photo_url": "new"}



def test_create() -> None:
    p = Profile(name="Foo")
    p.create()
    assert p.id
    assert Profile.get_by_id(p.id) == p

    with pytest.raises(AlreadyExists):
        Profile(id=p.id, name="Bar").create()
    assert (Profile.get_by_id(p.id)).name == "Foo"



def test_create_in_transaction() -> None:
    p = Profile(name="Foo")
    p.create()

    @transactional
    def create_in_transaction(transaction: Transaction) -> None:
        Profile(id=p.id, name="Bar").create(transaction=transaction)

    with pytest.raises(AlreadyExists):
        create_in_transaction(get_transaction())
    assert p.id
    assert (Profile.get_by_id(p.id)).name == "Foo"



def test_update() -> None:
    counter = Counter(totalCount=1, by_day={"mon": 1})
    counter.save()
    assert counter.id

    # Another writer changes a field this instance doesn't write
    Counter(id=counter.id, optional=7).save(exclude_unset=True, merge=True)

    counter.total = 5
    counter.stats.visits = 3
    counter.optional = None
    counter.update("total", "stats")
    assert get_stored_data(counter) == {
        "totalCount": 5,
        "stats": {"visits": 3},
        "by_day": {"mon": 1},
        "optional": 7,
    }

    # Without fields all of them are written
    counter.update()
    assert Counter.get_by_id(counter.id) == counter



def test_update_errors() -> None:
    with pytest.raises(ModelNotFoundError):
        Counter().update("total")

    with pytest.raises(NotFound):
        Counter(id=str(uuid4())).update("total")

    counter = Counter()
    counter.save()
    with pytest.raises(ValueError):
        counter.update("totalCount")



def test_update_in_transaction() -> None:
    counter = Counter()
    counter.save()

    @transactional
    def update_in_transaction(transaction: Transaction) -> None:
        counter.total = 2
        counter.update("total", transaction=transaction)

    update_in_transaction(get_transaction())
    assert counter.id
    assert (Counter.get_by_id(counter.id)).total == 2



def test_update_changes() -> None:
    counter = Counter(by_day={"mon": 1})
    counter.save()
    assert counter.id

    # Another writer changes a field this instance doesn't write
    Counter(id=counter.id, optional=7).save(exclude_unset=True, merge=True)

    counter.update({"totalCount": 4, "stats.visits": 2, "by_day.tue": 5})
    assert counter.total == 4
    assert counter.stats.visits == 2
    assert counter.by_day == {"mon": 1, "tue": 5}
    assert get_stored_data(counter) == {
        "totalCount": 4,
        "stats": {"visits": 2},
        "by_day": {"mon": 1, "tue": 5},
        "optional": 7,
    }

    # Values are serialized like save() does
    counter.update({"stats": CounterStats(visits=9)})
    assert counter.stats.visits == 9
    assert (get_stored_data(counter))["stats"] == {"visits": 9}  # type: ignore[index]



def test_update_changes_validation() -> None:
    counter = Counter(totalCount=1)
    counter.save()

    with pytest.raises(ValidationError):
        counter.update({"totalCount": "many"})
    assert counter.total == 1
    assert (get_stored_data(counter))["totalCount"] == 1  # type: ignore[index]



def test_update_changes_transforms() -> None:
    counter = Counter(totalCount=1, optional=3)
    counter.save()
    assert counter.id

    counter.update({"totalCount": Increment(2), "optional": DELETE_FIELD})
    assert counter.total == 3
    assert "optional" not in get_stored_data(counter)  # type: ignore[operator]
    counter.reload()
    assert counter.optional is None



def test_update_changes_errors() -> None:
    with pytest.raises(ModelNotFoundError):
        Counter().update({"totalCount": 1})

    with pytest.raises(NotFound):
        Counter(id=str(uuid4())).update({"totalCount": 1})

    counter = Counter()
    counter.save()
    with pytest.raises(TypeError):
        counter.update({"totalCount": 1}, "total")  # type: ignore[call-overload]



def test_update_changes_in_transaction() -> None:
    counter = Counter()
    counter.save()

    @transactional
    def update_in_transaction(transaction: Transaction) -> None:
        counter.update({"stats.visits": 2}, transaction=transaction)

    update_in_transaction(get_transaction())

    # The write happens on commit, so the instance is not changed
    assert counter.stats.visits == 0
    counter.reload()
    assert counter.stats.visits == 2



def test_batch(create_product) -> None:
    existing = create_product(product_id="existing", stock=1)
    to_delete = create_product(product_id="to-delete")
    assert existing.id and to_delete.id

    batch = get_batch()
    new = Product(product_id="new", price=1.0, stock=5)
    new.save(batch=batch)
    created = Product(product_id="created", price=1.0, stock=6)
    created.create(batch=batch)
    existing.update({"price": 9.0}, batch=batch)
    existing.increment("stock", 2, batch=batch)
    to_delete.delete(batch=batch)

    # The IDs are known right away, but nothing is written until the commit
    assert new.id and created.id
    assert Product.count() == 2
    # Like in a transaction, the instance is left unchanged
    assert existing.price == 1.23
    assert existing.stock == 1

    batch.commit()
    assert Product.get_by_id(new.id) == new
    assert Product.get_by_id(created.id) == created
    stored = Product.get_by_id(existing.id)
    assert (stored.price, stored.stock) == (9.0, 3)
    assert Product.get_by_ids([to_delete.id]) == []



def test_batch_is_atomic(create_product) -> None:
    existing = create_product(product_id="existing")

    batch = get_batch()
    Product(product_id="new", price=1.0, stock=1).save(batch=batch)
    Product(id=existing.id, product_id="dupe", price=1.0, stock=1).create(batch=batch)

    with pytest.raises(AlreadyExists):
        batch.commit()
    assert [p.product_id for p in Product.find()] == ["existing"]



def test_batch_and_transaction() -> None:
    product = Product(product_id="p", price=1.0, stock=1)
    with pytest.raises(ValueError):
        product.save(transaction=get_transaction(), batch=get_batch())



def test_find_not_in(create_company) -> None:
    ids = ["1234555-1", "1234567-8", "2131232-4", "4124432-4"]
    for company_id in ids:
        create_company(company_id=company_id)

    found = Company.find(
        {
            "company_id": {
                op.NOT_IN: [
                    "1234555-1",
                    "1234567-8",
                ]
            }
        }
    )
    assert len(found) == 2
    for company in found:
        assert company.company_id in ("2131232-4", "4124432-4")



def test_find_array_contains(create_todolist) -> None:
    list_1 = create_todolist("list_1", ["Work", "Eat", "Sleep"])
    create_todolist("list_2", ["Learn Python", "Walk the dog"])

    found = TodoList.find({"items": {op.ARRAY_CONTAINS: "Eat"}})
    assert len(found) == 1
    assert found[0].name == list_1.name



def test_find_array_contains_any(create_todolist) -> None:
    list_1 = create_todolist("list_1", ["Work", "Eat"])
    list_2 = create_todolist("list_2", ["Relax", "Chill", "Sleep"])
    create_todolist("list_3", ["Learn Python", "Walk the dog"])

    found = TodoList.find({"items": {op.ARRAY_CONTAINS_ANY: ["Eat", "Sleep"]}})
    assert len(found) == 2
    for lst in found:
        assert lst.name in (list_1.name, list_2.name)



def test_find_limit(create_company) -> None:
    ids = ["1234555-1", "1234567-8", "2131232-4", "4124432-4"]
    for company_id in ids:
        create_company(company_id=company_id)

    companies_all = Company.find()
    assert len(companies_all) == 4

    companies_2 = Company.find(limit=2)
    assert len(companies_2) == 2



def test_find_order_by(create_company) -> None:
    companies_and_owners = [
        {"company_id": "1234555-1", "last_name": "A", "first_name": "A"},
        {"company_id": "1234555-2", "last_name": "A", "first_name": "B"},
        {"company_id": "1234567-8", "last_name": "B", "first_name": "C"},
        {"company_id": "1234567-9", "last_name": "B", "first_name": "D"},
        {"company_id": "2131232-4", "last_name": "C", "first_name": "E"},
        {"company_id": "2131232-5", "last_name": "C", "first_name": "F"},
        {"company_id": "4124432-4", "last_name": "D", "first_name": "G"},
        {"company_id": "4124432-5", "last_name": "D", "first_name": "H"},
    ]

    companies_and_owners = [create_company(**item) for item in companies_and_owners]

    companies_ascending = Company.find(order_by=[("owner.first_name", Query.ASCENDING)])
    assert companies_ascending == companies_and_owners

    companies_descending = Company.find(order_by=[("owner.first_name", Query.DESCENDING)])
    reversed_companies_and_owners = list(reversed(companies_and_owners))
    assert companies_descending == reversed_companies_and_owners

    lastname_ascending_firstname_descending = Company.find(
        order_by=[
            ("owner.last_name", Query.ASCENDING),
            ("owner.first_name", Query.DESCENDING),
        ]
    )
    expected = sorted(companies_and_owners, key=attrgetter("owner.first_name"), reverse=True)
    expected = sorted(expected, key=attrgetter("owner.last_name"))
    assert expected == lastname_ascending_firstname_descending

    lastname_ascending_firstname_ascending = Company.find(
        order_by=[
            ("owner.last_name", Query.ASCENDING),
            ("owner.first_name", Query.ASCENDING),
        ]
    )
    assert companies_and_owners == lastname_ascending_firstname_ascending



def test_find_offset(create_company) -> None:
    ids_and_lastnames = (
        ("1234555-1", "A"),
        ("1234567-8", "B"),
        ("2131232-4", "C"),
        ("4124432-4", "D"),
    )
    for company_id, lastname in ids_and_lastnames:
        create_company(company_id=company_id, last_name=lastname)
    companies_ascending = Company.find(
        order_by=[("owner.last_name", Query.ASCENDING)], offset=2
    )
    assert companies_ascending[0].owner.last_name == "C"
    assert companies_ascending[1].owner.last_name == "D"
    assert len(companies_ascending) == 2



def test_get_by_id(create_company) -> None:
    c: Company = create_company(company_id="1234567-8")

    assert c.id is not None
    assert c.company_id == "1234567-8"
    assert c.owner.last_name == "Doe"

    c_2 = Company.get_by_id(c.id)

    assert c_2.id == c.id
    assert c_2.company_id == "1234567-8"
    assert c_2.owner.first_name == "John"



def test_get_by_empty_str_id() -> None:
    with pytest.raises(ModelNotFoundError):
        Company.get_by_id("")



def test_missing_collection() -> None:
    class User(Model):
        name: str
        # normally __collection__ would be defined here

    with pytest.raises(CollectionNotDefined):
        User(name="John").save()



def test_model_aliases() -> None:
    class User(Model):
        __collection__ = "User"

        first_name: str = Field(..., alias="firstName")
        city: str

    user = User(firstName="John", city="Helsinki")
    user.save()
    assert user.id

    user_from_db = User.get_by_id(user.id)
    assert user_from_db.first_name == "John"
    assert user_from_db.city == "Helsinki"



@pytest.mark.parametrize(
    "model_id",
    [
        "abc",
        pytest.param("a" * 1500, id="1500 chars"),
        "...",
        ".foo",
        "..bar",
        "f..oo",
        "bar..",
        "__",
        "___",
        "_foo_",
        "__bar_",
        "_baz__",
        "b__a__r",
        "å",
        "😀",
        " ",
        '"',
        "'",
        "\\",
        "\x00",
        "\x01",
        "\x07",
        "!:&+-*'()",
    ],
)
def test_models_with_valid_custom_id(model_id) -> None:
    product_id = str(uuid4())

    product = Product(product_id=product_id, price=123.45, stock=2)
    product.id = model_id
    product.save()

    found = Product.get_by_id(model_id)
    assert found.product_id == product_id

    found.delete()



@pytest.mark.parametrize(
    "model_id",
    [
        "",
        pytest.param("a" * 1501, id="1501 chars"),
        ".",
        "..",
        "____",
        "__foo__",
        "__😀__",
        "/",
        "foo/bar",
        "foo/bar/baz",
    ],
)
def test_models_with_invalid_custom_id(model_id: str) -> None:
    product = Product(product_id="product 123", price=123.45, stock=2)
    product.id = model_id
    with pytest.raises(InvalidDocumentID):
        product.save()

    with pytest.raises(ModelNotFoundError):
        Product.get_by_id(model_id)



def test_truncate_collection(create_company) -> None:
    create_company(company_id="1234567-8")
    create_company(company_id="1234567-9")

    companies = Company.find({})
    assert len(companies) == 2

    Company.truncate_collection()
    new_companies = Company.find({})
    assert len(new_companies) == 0



def test_custom_id_model() -> None:
    c = CustomIDModel(bar="bar")  # type: ignore
    c.save()

    models = CustomIDModel.find({})
    assert len(models) == 1

    m = models[0]
    assert m.foo is not None
    assert m.bar == "bar"




def test_custom_id_model_get_by_doc_ids() -> None:
    c = CustomIDModel(bar="bar")  # type: ignore
    c.save()
    assert c.foo

    models = CustomIDModel.get_by_doc_ids([c.foo, "missing"])
    assert [(m.foo, m.bar) for m in models] == [(c.foo, "bar")]


def test_custom_id_conflict() -> None:
    CustomIDConflictModel(foo="foo", bar="bar").save()

    models = CustomIDModel.find({})
    assert len(models) == 1

    m = models[0]
    assert m.foo != "foo"
    assert m.bar == "bar"



def test_model_id_persistency() -> None:
    c = CustomIDConflictModel(foo="foo", bar="bar")
    c.save()
    assert c.id

    c = CustomIDConflictModel.get_by_doc_id(c.id)
    c.save()

    assert len(CustomIDConflictModel.find({})) == 1



def test_bare_model_document_id_persistency() -> None:
    c = CustomIDModel(bar="bar")  # type: ignore
    c.save()
    assert c.foo

    c = CustomIDModel.get_by_doc_id(c.foo)
    c.save()

    assert len(CustomIDModel.find({})) == 1



def test_bare_model_get_by_empty_doc_id() -> None:
    with pytest.raises(ModelNotFoundError):
        CustomIDModel.get_by_doc_id("")



def test_extra_fields() -> None:
    CustomIDModelExtra(foo="foo", bar="bar", baz="baz").save()  # type: ignore
    with pytest.raises(ValidationError):
        CustomIDModel.find({})



def test_company_stats(create_company) -> None:
    company: Company = create_company(company_id="1234567-8")
    company_stats = company.stats()

    stats = company_stats.get_stats()
    stats.sales = 100
    stats.save()

    # Ensure the data can be still loaded
    loaded = company.stats().get_stats()
    assert loaded.sales == stats.sales

    # And that we can still save
    loaded.sales += 1
    loaded.save()

    stats = company_stats.get_stats()
    assert stats.sales == 101



def test_subcollection_model_safety() -> None:
    """
    Ensure you shouldn't be able to use unprepared subcollection models accidentally
    """
    with pytest.raises(CollectionNotDefined):
        UserStats.find({})



def test_get_user_purchases() -> None:
    u = User(name="Foo")
    u.save()
    assert u.id

    us = UserStats.model_for(u)
    us(id="2021", purchases=42).save()

    assert get_user_purchases(u.id) == 42



def test_reload() -> None:
    u = User(name="Foo")
    u.save()

    # change the value in the database
    u_ = User.find_one({"name": "Foo"})
    u_.name = "Bar"
    u_.save()

    assert u.name == "Foo"
    u.reload()
    assert u.name == "Bar"

    another_user = User(name="Another")
    with pytest.raises(ModelNotFoundError):
        another_user.reload()



def test_save_with_exclude_none() -> None:
    p = Profile(name="Foo")
    p.save(exclude_none=True)

    document_id = p.get_document_id()
    assert document_id

    # pylint: disable=protected-access
    document = Profile._get_col_ref().document(document_id).get()

    data = document.to_dict()  # type: ignore[union-attr]
    assert data == {"name": "Foo"}
    p.save()

    # pylint: disable=protected-access
    document = Profile._get_col_ref().document(document_id).get()

    data = document.to_dict()  # type: ignore[union-attr]
    assert data == {"name": "Foo", "photo_url": None}



def test_save_with_exclude_unset() -> None:
    p = Profile(photo_url=None)
    p.save(exclude_unset=True)

    document_id = p.get_document_id()
    assert document_id

    # pylint: disable=protected-access
    document = Profile._get_col_ref().document(document_id).get()

    data = document.to_dict()  # type: ignore[union-attr]
    assert data == {"photo_url": None}
    p.save()

    # pylint: disable=protected-access
    document = Profile._get_col_ref().document(document_id).get()

    data = document.to_dict()  # type: ignore[union-attr]
    assert data == {"name": "", "photo_url": None}



def test_update_city_in_transaction() -> None:
    """
    Test updating a model in a transaction. Test case from README.
    """

    @transactional
    def decrement_population(transaction: Transaction, city: City, decrement: int = 1):
        city.reload(transaction=transaction)
        city.population = max(0, city.population - decrement)
        city.save(transaction=transaction)

    c = City(id="SF", population=1)
    c.save()
    c.increment_population(increment=1)
    assert c.population == 2

    t = get_transaction()
    decrement_population(transaction=t, city=c, decrement=5)
    assert c.population == 0



def test_delete_in_transaction(create_company) -> None:
    """
    Test deleting a Company model within a Firestore transaction.
    """
    # Create a company
    company: Company = create_company(
        company_id="11223344-4", first_name="Joe", last_name="Day"
    )
    _id = company.id
    assert _id

    @transactional
    def delete_company(transaction: Transaction) -> None:
        company.delete()

    # Call the transactional function
    t = get_transaction()
    delete_company(t)

    # Outside the transaction, the deletion should now be committed
    with pytest.raises(ModelNotFoundError):
        Company.get_by_id(_id)



def test_delete_model(create_company) -> None:
    company: Company = create_company(
        company_id="11223344-5", first_name="Jane", last_name="Doe"
    )

    _id = company.id
    assert _id

    company.delete()

    with pytest.raises(ModelNotFoundError):
        Company.get_by_id(_id)



def test_update_model_in_transaction() -> None:
    """
    Test updating a model in a transaction.
    """

    @transactional
    def update_in_transaction(
        transaction: Transaction, profile_id: str, name: str
    ) -> None:
        """Updates a Profile in a transaction."""
        profile = Profile(id=profile_id)
        profile.reload(transaction=transaction)
        profile.name = name
        profile.save(transaction=transaction)

    p = Profile(name="Foo")
    p.save()

    t = get_transaction()
    update_in_transaction(t, p.id, name="Bar")
    p.reload()
    assert p.name == "Bar"



def test_update_submodel_in_transaction() -> None:
    """
    Test Updating a submodel in a transaction.
    """

    @transactional
    def update_submodel_in_transaction(
        transaction: Transaction, user_id: str, period: str
    ) -> UserStats:
        """Updates a UserStats in a transaction."""
        u = User.get_by_id(user_id, transaction=transaction)
        us = UserStats.model_for(u)
        user_stats: UserStats = us.get_by_id(period)  # pylint: disable=no-member
        user_stats.purchases += 1
        user_stats.save(transaction=transaction)
        return user_stats

    u = User(name="Foo")
    u.save()
    assert u.id
    us = UserStats.model_for(u)
    us(id="2021", purchases=42).save()  # pylint: disable=no-member

    t = get_transaction()
    user_stats = update_submodel_in_transaction(t, u.id, "2021")
    assert isinstance(user_stats, UserStats)
    assert user_stats.purchases == 43
    assert get_user_purchases(u.id) == 43



def test_model_for_validates_template_values():
    class Org(Model):
        __collection__ = "orgs"
        slug: str

    class OrgItem(SubModel):
        id: Optional[str] = None
        value: int = 0

        class Collection(SubCollection):
            __collection_tpl__ = "orgs/{slug}/items"

    assert OrgItem.model_for(Org(slug="acme")).get_collection_name().endswith("orgs/acme/items")

    # A value with a slash would point at another document's subcollection
    with pytest.raises(InvalidDocumentID, match="orgs/{slug}/items"):
        OrgItem.model_for(Org(slug="victim/items/evil"))
    for bad in ("", ".", "..", "__x__"):
        with pytest.raises(InvalidDocumentID):
            OrgItem.model_for(Org(slug=bad))

    # An unsaved parent has no ID to build the path from
    with pytest.raises(InvalidDocumentID, match="None"):
        UserStats.model_for(User(name="unsaved"))



def test_save_with_aliased_document_id():
    class AliasedIdModel(Model):
        __collection__ = "aliasedIdModels"
        model_config = ConfigDict(populate_by_name=True)

        id: Optional[str] = Field(default=None, alias="docId")
        name: str

    model = AliasedIdModel(name="x")
    model.save()
    assert model.id

    # The ID is the document name only, not stored under its alias in the data
    snapshot = AliasedIdModel._get_col_ref().document(model.id).get()
    assert snapshot.to_dict() == {"name": "x"}

    loaded = AliasedIdModel.get_by_id(model.id)
    assert loaded.id == model.id
    assert loaded.name == "x"
