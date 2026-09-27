from operator import attrgetter
from typing import Dict, List, Optional
from uuid import uuid4

import pytest
from google.api_core.exceptions import AlreadyExists, NotFound
from google.cloud.firestore import Query, async_transactional
from google.cloud.firestore_v1 import DELETE_FIELD, Increment
from google.cloud.firestore_v1.async_transaction import AsyncTransaction
from pydantic import BaseModel, ConfigDict, Field, ValidationError

import firedantic.operators as op
from firedantic import (
    AsyncModel,
    AsyncSubCollection,
    AsyncSubModel,
    get_async_batch,
    get_async_transaction,
)
from firedantic.configurations import configuration
from firedantic.exceptions import (
    CollectionNotDefined,
    InvalidDocumentID,
    ModelNotFoundError,
)
from firedantic.tests.tests_async.conftest import (
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


@pytest.mark.asyncio
async def test_save_model(create_company) -> None:
    company = await create_company()

    assert company.id is not None
    assert company.owner.first_name == "John"
    assert company.owner.last_name == "Doe"


@pytest.mark.asyncio
async def test_find_one(create_company) -> None:
    with pytest.raises(ModelNotFoundError):
        await Company.find_one()

    company_a: Company = await create_company(company_id="1234555-1", first_name="Foo")
    company_b: Company = await create_company(company_id="1231231-2", first_name="Bar")

    a: Company = await Company.find_one({"company_id": company_a.company_id})
    b: Company = await Company.find_one({"company_id": company_b.company_id})

    assert a.company_id == company_a.company_id
    assert b.company_id == company_b.company_id
    assert a.owner.first_name == "Foo"
    assert b.owner.first_name == "Bar"

    with pytest.raises(ModelNotFoundError):
        await Company.find_one({"company_id": "Foo"})

    random_company = await Company.find_one()
    assert random_company.company_id in {a.company_id, b.company_id}

    first_asc = await Company.find_one(order_by=[("owner.first_name", Query.ASCENDING)])
    assert first_asc.owner.first_name == "Bar"

    first_desc = await Company.find_one(order_by=[("owner.first_name", Query.DESCENDING)])
    assert first_desc.owner.first_name == "Foo"


@pytest.mark.asyncio
async def test_find(create_company, create_product) -> None:
    ids = ["1234555-1", "1234567-8", "2131232-4", "4124432-4"]
    for company_id in ids:
        await create_company(company_id=company_id)

    c = await Company.find({"company_id": "4124432-4"})
    assert c[0].company_id == "4124432-4"
    assert c[0].owner.first_name == "John"

    d = await Company.find({"owner.first_name": "John"})
    assert len(d) == 4

    d = await Company.find({"owner.first_name": {op.EQ: "John"}})
    assert len(d) == 4

    d = await Company.find({"owner.first_name": {"==": "John"}})
    assert len(d) == 4

    for p in TEST_PRODUCTS:
        await create_product(**p)

    assert len(await Product.find({})) == 4

    products = await Product.find({"stock": {op.GTE: 1}})
    assert len(products) == 3

    products = await Product.find({"stock": {op.GTE: 2, op.LT: 4}})
    assert len(products) == 2

    products = await Product.find({"product_id": {op.IN: ["a", "d", "g"]}})
    assert len(products) == 2

    with pytest.raises(ValueError):
        await Product.find({"product_id": {"<>": "a"}})



@pytest.mark.asyncio
async def test_count(create_product) -> None:
    assert await Product.count() == 0

    for p in TEST_PRODUCTS:
        await create_product(**p)

    assert await Product.count() == 4
    assert await Product.count({"stock": {op.GTE: 1}}) == 3
    assert await Product.count({"stock": {op.GTE: 2, op.LT: 4}}) == 2
    assert await Product.count({"product_id": "missing"}) == 0


@pytest.mark.asyncio
async def test_sum_and_avg(create_product) -> None:
    assert await Product.sum("stock") == 0
    assert await Product.avg("stock") is None

    for p in TEST_PRODUCTS:
        await create_product(**p, price=1.5)

    assert await Product.sum("stock") == 6
    assert await Product.sum("stock", {"stock": {op.GTE: 2}}) == 5
    assert await Product.sum("price") == 6.0
    assert await Product.avg("stock") == 1.5
    assert await Product.avg("stock", {"stock": {op.GTE: 2}}) == 2.5
    assert await Product.avg("stock", {"product_id": "missing"}) is None


@pytest.mark.asyncio
async def test_sum_and_avg_paths() -> None:
    await Counter(totalCount=1, stats=CounterStats(visits=2)).save()
    await Counter(totalCount=3, stats=CounterStats(visits=4)).save()
    # Values that aren't numbers are ignored
    await Counter(id="no-count").save()
    await Counter(id="no-count", optional=1).save(exclude_unset=True)

    assert await Counter.sum("totalCount") == 4
    assert await Counter.sum("stats.visits") == 6
    assert await Counter.avg("totalCount") == 2.0
    assert await Counter.avg("optional") == 1.0
    assert await Counter.avg("missing") is None


@pytest.mark.asyncio
async def test_sum_and_avg_in_transaction(create_product) -> None:
    await create_product(stock=2)
    await create_product(stock=4)

    @async_transactional
    async def read_in_transaction(transaction: AsyncTransaction):
        return (
            await Product.sum("stock", transaction=transaction),
            await Product.avg("stock", transaction=transaction),
        )

    assert await read_in_transaction(get_async_transaction()) == (6, 3.0)


@pytest.mark.asyncio
async def test_find_pagination(create_product) -> None:
    for p in TEST_PRODUCTS:
        await create_product(**p)
    order_by = [("stock", Query.ASCENDING)]

    page_1 = await Product.find(order_by=order_by, limit=2)
    page_2 = await Product.find(order_by=order_by, limit=2, start_after=page_1[-1])
    # The document ID or path works as a cursor too, e.g. when passed through an API
    page_2_by_id = await Product.find(order_by=order_by, limit=2, start_after=page_1[-1].id)
    page_2_by_path = await Product.find(
        order_by=order_by, limit=2, start_after=page_1[-1].get_document_path()
    )
    page_3 = await Product.find(order_by=order_by, limit=2, start_after=page_2[-1])

    assert [p.product_id for p in page_1] == ["a", "b"]
    assert [p.product_id for p in page_2] == ["c", "d"]
    assert page_2_by_id == page_2
    assert page_2_by_path == page_2
    assert page_3 == []


@pytest.mark.asyncio
async def test_find_pagination_with_filter_and_ties(create_product) -> None:
    for _ in range(5):
        await create_product(stock=1)
    await create_product(stock=0)

    # Without order_by the pages are ordered by the inequality field and document ID
    found = []
    page = await Product.find({"stock": {op.GTE: 1}}, limit=2)
    while page:
        found.extend(page)
        page = await Product.find({"stock": {op.GTE: 1}}, limit=2, start_after=page[-1])

    assert len(found) == 5
    assert len({p.id for p in found}) == 5


@pytest.mark.asyncio
async def test_find_pagination_missing_cursor() -> None:
    with pytest.raises(ModelNotFoundError):
        await Product.find(start_after="missing")


@pytest.mark.asyncio
async def test_find_or(create_product) -> None:
    for p in TEST_PRODUCTS:
        await create_product(**p)

    async def find_ids(filter_: Dict) -> List[str]:
        return sorted(p.product_id for p in await Product.find(filter_))

    assert await find_ids({op.OR: [{"stock": 0}, {"stock": {op.GTE: 3}}]}) == ["a", "d"]
    # Top-level keys and the keys of each clause are combined with AND
    assert await find_ids(
        {"product_id": {op.IN: ["a", "b", "c"]}, op.OR: [{"stock": 0}, {"stock": 3}]}
    ) == ["a"]
    assert await find_ids({op.OR: [{"stock": {op.GTE: 1}, "product_id": "b"}, {"product_id": "a"}]}) == [
        "a",
        "b",
    ]
    # Nested AND and OR
    assert await find_ids(
        {
            op.OR: [
                {op.AND: [{"stock": {op.GTE: 1}}, {"stock": {op.LT: 2}}]},
                {op.OR: [{"product_id": "d"}]},
            ]
        }
    ) == ["b", "d"]

    assert await Product.count({op.OR: [{"stock": 0}, {"stock": 3}]}) == 2
    assert await Product.sum("stock", {op.OR: [{"stock": 1}, {"stock": 3}]}) == 4
    found = await Product.find_one({op.OR: [{"product_id": "c"}, {"product_id": "missing"}]})
    assert found.product_id == "c"


@pytest.mark.asyncio
async def test_find_or_invalid() -> None:
    filters: List[Dict] = [
        {op.OR: []},
        {op.OR: {"stock": 1}},
        {op.OR: [{}]},
        {op.AND: ["stock"]},
        {op.OR: [{"stock": {"~": 1}}]},
    ]
    for filter_ in filters:
        with pytest.raises(ValueError):
            await Product.find(filter_)


@pytest.mark.asyncio
async def test_get_by_ids(create_company) -> None:
    company_a = await create_company(company_id="1234555-1")
    company_b = await create_company(company_id="1231231-2")

    found = await Company.get_by_ids([company_b.id, "missing", company_a.id, company_b.id])
    assert found == [company_b, company_a]

    assert await Company.get_by_ids([]) == []
    with pytest.raises(ModelNotFoundError):
        await Company.get_by_ids([company_a.id, "a/b"])


@pytest.mark.asyncio
async def test_submodel_get_by_ids() -> None:
    u = User(name="Foo")
    await u.save()
    us = UserStats.model_for(u)
    await us(id="2021", purchases=1).save()
    await us(id="2022", purchases=2).save()

    found = await us.get_by_ids(["2022", "2020", "2021"])
    assert [(s.id, s.purchases) for s in found] == [("2022", 2), ("2021", 1)]


@pytest.mark.asyncio
async def test_count_and_get_by_ids_in_transaction(create_company) -> None:
    company = await create_company()

    @async_transactional
    async def read_in_transaction(transaction: AsyncTransaction):
        return (
            await Company.count(transaction=transaction),
            await Company.get_by_ids([company.id], transaction=transaction),
        )

    assert await read_in_transaction(get_async_transaction()) == (1, [company])


class CounterStats(BaseModel):
    visits: int = 0


class Counter(AsyncModel):
    __collection__ = "counters"
    model_config = ConfigDict(populate_by_name=True)

    total: int = Field(default=0, alias="totalCount")
    stats: CounterStats = CounterStats()
    by_day: Dict[str, int] = {}
    optional: Optional[int] = None


@pytest.mark.asyncio
async def test_increment(create_product) -> None:
    product = await create_product(stock=3)
    assert product.id

    await product.increment("stock", 5)
    assert product.stock == 8
    assert (await Product.get_by_id(product.id)).stock == 8

    await product.increment("stock", -3)
    await product.reload()
    assert product.stock == 5


@pytest.mark.asyncio
async def test_increment_paths() -> None:
    counter = Counter(by_day={"mon": 1})
    await counter.save(exclude_none=True)
    assert counter.id

    # Aliased, nested, dict and missing fields are updated the way Firestore does
    await counter.increment("totalCount")
    await counter.increment("stats.visits", 2)
    await counter.increment("by_day.mon", 3)
    await counter.increment("by_day.tue", 5)
    await counter.increment("optional", 4)

    assert counter.total == 1
    assert counter.stats.visits == 2
    assert counter.by_day == {"mon": 4, "tue": 5}
    assert counter.optional == 4
    assert counter == await Counter.get_by_id(counter.id)


@pytest.mark.asyncio
async def test_increment_unsaved() -> None:
    with pytest.raises(ModelNotFoundError):
        await Counter().increment("totalCount")


@pytest.mark.asyncio
async def test_increment_in_transaction() -> None:
    counter = Counter()
    await counter.save()

    @async_transactional
    async def increment_in_transaction(transaction: AsyncTransaction) -> None:
        await counter.increment("totalCount", 2, transaction=transaction)

    await increment_in_transaction(get_async_transaction())

    # The write happens on commit, so the instance is not changed
    assert counter.total == 0
    await counter.reload()
    assert counter.total == 2

async def get_stored_data(model: AsyncModel) -> Optional[Dict]:
    # pylint: disable=protected-access
    return (await model._get_doc_ref().get()).to_dict()


@pytest.mark.asyncio
async def test_save_merge() -> None:
    p = Profile(name="Foo", photo_url="old")
    await p.save()
    assert p.id

    # Only the set fields are written, the stored name is kept
    await Profile(id=p.id, photo_url="new").save(exclude_unset=True, merge=True)
    assert await get_stored_data(p) == {"name": "Foo", "photo_url": "new"}

    # Without merge the document is replaced
    await Profile(id=p.id, photo_url="newer").save(exclude_unset=True)
    assert await get_stored_data(p) == {"photo_url": "newer"}


@pytest.mark.asyncio
async def test_save_merge_in_transaction() -> None:
    p = Profile(name="Foo", photo_url="old")
    await p.save()

    @async_transactional
    async def save_in_transaction(transaction: AsyncTransaction) -> None:
        await Profile(id=p.id, photo_url="new").save(
            exclude_unset=True, merge=True, transaction=transaction
        )

    await save_in_transaction(get_async_transaction())
    assert await get_stored_data(p) == {"name": "Foo", "photo_url": "new"}


@pytest.mark.asyncio
async def test_create() -> None:
    p = Profile(name="Foo")
    await p.create()
    assert p.id
    assert await Profile.get_by_id(p.id) == p

    with pytest.raises(AlreadyExists):
        await Profile(id=p.id, name="Bar").create()
    assert (await Profile.get_by_id(p.id)).name == "Foo"


@pytest.mark.asyncio
async def test_create_in_transaction() -> None:
    p = Profile(name="Foo")
    await p.create()

    @async_transactional
    async def create_in_transaction(transaction: AsyncTransaction) -> None:
        await Profile(id=p.id, name="Bar").create(transaction=transaction)

    with pytest.raises(AlreadyExists):
        await create_in_transaction(get_async_transaction())
    assert p.id
    assert (await Profile.get_by_id(p.id)).name == "Foo"


@pytest.mark.asyncio
async def test_update() -> None:
    counter = Counter(totalCount=1, by_day={"mon": 1})
    await counter.save()
    assert counter.id

    # Another writer changes a field this instance doesn't write
    await Counter(id=counter.id, optional=7).save(exclude_unset=True, merge=True)

    counter.total = 5
    counter.stats.visits = 3
    counter.optional = None
    await counter.update("total", "stats")
    assert await get_stored_data(counter) == {
        "totalCount": 5,
        "stats": {"visits": 3},
        "by_day": {"mon": 1},
        "optional": 7,
    }

    # Without fields all of them are written
    await counter.update()
    assert await Counter.get_by_id(counter.id) == counter


@pytest.mark.asyncio
async def test_update_errors() -> None:
    with pytest.raises(ModelNotFoundError):
        await Counter().update("total")

    with pytest.raises(NotFound):
        await Counter(id=str(uuid4())).update("total")

    counter = Counter()
    await counter.save()
    with pytest.raises(ValueError):
        await counter.update("totalCount")


@pytest.mark.asyncio
async def test_update_in_transaction() -> None:
    counter = Counter()
    await counter.save()

    @async_transactional
    async def update_in_transaction(transaction: AsyncTransaction) -> None:
        counter.total = 2
        await counter.update("total", transaction=transaction)

    await update_in_transaction(get_async_transaction())
    assert counter.id
    assert (await Counter.get_by_id(counter.id)).total == 2


@pytest.mark.asyncio
async def test_update_changes() -> None:
    counter = Counter(by_day={"mon": 1})
    await counter.save()
    assert counter.id

    # Another writer changes a field this instance doesn't write
    await Counter(id=counter.id, optional=7).save(exclude_unset=True, merge=True)

    await counter.update({"totalCount": 4, "stats.visits": 2, "by_day.tue": 5})
    assert counter.total == 4
    assert counter.stats.visits == 2
    assert counter.by_day == {"mon": 1, "tue": 5}
    assert await get_stored_data(counter) == {
        "totalCount": 4,
        "stats": {"visits": 2},
        "by_day": {"mon": 1, "tue": 5},
        "optional": 7,
    }

    # Values are serialized like save() does
    await counter.update({"stats": CounterStats(visits=9)})
    assert counter.stats.visits == 9
    assert (await get_stored_data(counter))["stats"] == {"visits": 9}  # type: ignore[index]


@pytest.mark.asyncio
async def test_update_changes_validation() -> None:
    counter = Counter(totalCount=1)
    await counter.save()

    with pytest.raises(ValidationError):
        await counter.update({"totalCount": "many"})
    assert counter.total == 1
    assert (await get_stored_data(counter))["totalCount"] == 1  # type: ignore[index]


@pytest.mark.asyncio
async def test_update_changes_transforms() -> None:
    counter = Counter(totalCount=1, optional=3)
    await counter.save()
    assert counter.id

    await counter.update({"totalCount": Increment(2), "optional": DELETE_FIELD})
    assert counter.total == 3
    assert "optional" not in await get_stored_data(counter)  # type: ignore[operator]
    await counter.reload()
    assert counter.optional is None


@pytest.mark.asyncio
async def test_update_changes_errors() -> None:
    with pytest.raises(ModelNotFoundError):
        await Counter().update({"totalCount": 1})

    with pytest.raises(NotFound):
        await Counter(id=str(uuid4())).update({"totalCount": 1})

    counter = Counter()
    await counter.save()
    with pytest.raises(TypeError):
        await counter.update({"totalCount": 1}, "total")  # type: ignore[call-overload]


@pytest.mark.asyncio
async def test_update_changes_in_transaction() -> None:
    counter = Counter()
    await counter.save()

    @async_transactional
    async def update_in_transaction(transaction: AsyncTransaction) -> None:
        await counter.update({"stats.visits": 2}, transaction=transaction)

    await update_in_transaction(get_async_transaction())

    # The write happens on commit, so the instance is not changed
    assert counter.stats.visits == 0
    await counter.reload()
    assert counter.stats.visits == 2


@pytest.mark.asyncio
async def test_batch(create_product) -> None:
    existing = await create_product(product_id="existing", stock=1)
    to_delete = await create_product(product_id="to-delete")
    assert existing.id and to_delete.id

    batch = get_async_batch()
    new = Product(product_id="new", price=1.0, stock=5)
    await new.save(batch=batch)
    created = Product(product_id="created", price=1.0, stock=6)
    await created.create(batch=batch)
    await existing.update({"price": 9.0}, batch=batch)
    await existing.increment("stock", 2, batch=batch)
    await to_delete.delete(batch=batch)

    # The IDs are known right away, but nothing is written until the commit
    assert new.id and created.id
    assert await Product.count() == 2
    # Like in a transaction, the instance is left unchanged
    assert existing.price == 1.23
    assert existing.stock == 1

    await batch.commit()
    assert await Product.get_by_id(new.id) == new
    assert await Product.get_by_id(created.id) == created
    stored = await Product.get_by_id(existing.id)
    assert (stored.price, stored.stock) == (9.0, 3)
    assert await Product.get_by_ids([to_delete.id]) == []


@pytest.mark.asyncio
async def test_batch_is_atomic(create_product) -> None:
    existing = await create_product(product_id="existing")

    batch = get_async_batch()
    await Product(product_id="new", price=1.0, stock=1).save(batch=batch)
    await Product(id=existing.id, product_id="dupe", price=1.0, stock=1).create(batch=batch)

    with pytest.raises(AlreadyExists):
        await batch.commit()
    assert [p.product_id for p in await Product.find()] == ["existing"]


@pytest.mark.asyncio
async def test_batch_and_transaction() -> None:
    product = Product(product_id="p", price=1.0, stock=1)
    with pytest.raises(ValueError):
        await product.save(transaction=get_async_transaction(), batch=get_async_batch())


@pytest.mark.asyncio
async def test_find_not_in(create_company) -> None:
    ids = ["1234555-1", "1234567-8", "2131232-4", "4124432-4"]
    for company_id in ids:
        await create_company(company_id=company_id)

    found = await Company.find(
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


@pytest.mark.asyncio
async def test_find_array_contains(create_todolist) -> None:
    list_1 = await create_todolist("list_1", ["Work", "Eat", "Sleep"])
    await create_todolist("list_2", ["Learn Python", "Walk the dog"])

    found = await TodoList.find({"items": {op.ARRAY_CONTAINS: "Eat"}})
    assert len(found) == 1
    assert found[0].name == list_1.name


@pytest.mark.asyncio
async def test_find_array_contains_any(create_todolist) -> None:
    list_1 = await create_todolist("list_1", ["Work", "Eat"])
    list_2 = await create_todolist("list_2", ["Relax", "Chill", "Sleep"])
    await create_todolist("list_3", ["Learn Python", "Walk the dog"])

    found = await TodoList.find({"items": {op.ARRAY_CONTAINS_ANY: ["Eat", "Sleep"]}})
    assert len(found) == 2
    for lst in found:
        assert lst.name in (list_1.name, list_2.name)


@pytest.mark.asyncio
async def test_find_limit(create_company) -> None:
    ids = ["1234555-1", "1234567-8", "2131232-4", "4124432-4"]
    for company_id in ids:
        await create_company(company_id=company_id)

    companies_all = await Company.find()
    assert len(companies_all) == 4

    companies_2 = await Company.find(limit=2)
    assert len(companies_2) == 2


@pytest.mark.asyncio
async def test_find_order_by(create_company) -> None:
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

    companies_and_owners = [await create_company(**item) for item in companies_and_owners]

    companies_ascending = await Company.find(order_by=[("owner.first_name", Query.ASCENDING)])
    assert companies_ascending == companies_and_owners

    companies_descending = await Company.find(order_by=[("owner.first_name", Query.DESCENDING)])
    reversed_companies_and_owners = list(reversed(companies_and_owners))
    assert companies_descending == reversed_companies_and_owners

    lastname_ascending_firstname_descending = await Company.find(
        order_by=[
            ("owner.last_name", Query.ASCENDING),
            ("owner.first_name", Query.DESCENDING),
        ]
    )
    expected = sorted(companies_and_owners, key=attrgetter("owner.first_name"), reverse=True)
    expected = sorted(expected, key=attrgetter("owner.last_name"))
    assert expected == lastname_ascending_firstname_descending

    lastname_ascending_firstname_ascending = await Company.find(
        order_by=[
            ("owner.last_name", Query.ASCENDING),
            ("owner.first_name", Query.ASCENDING),
        ]
    )
    assert companies_and_owners == lastname_ascending_firstname_ascending


@pytest.mark.asyncio
async def test_find_offset(create_company) -> None:
    ids_and_lastnames = (
        ("1234555-1", "A"),
        ("1234567-8", "B"),
        ("2131232-4", "C"),
        ("4124432-4", "D"),
    )
    for company_id, lastname in ids_and_lastnames:
        await create_company(company_id=company_id, last_name=lastname)
    companies_ascending = await Company.find(
        order_by=[("owner.last_name", Query.ASCENDING)], offset=2
    )
    assert companies_ascending[0].owner.last_name == "C"
    assert companies_ascending[1].owner.last_name == "D"
    assert len(companies_ascending) == 2


@pytest.mark.asyncio
async def test_get_by_id(create_company) -> None:
    c: Company = await create_company(company_id="1234567-8")

    assert c.id is not None
    assert c.company_id == "1234567-8"
    assert c.owner.last_name == "Doe"

    c_2 = await Company.get_by_id(c.id)

    assert c_2.id == c.id
    assert c_2.company_id == "1234567-8"
    assert c_2.owner.first_name == "John"


@pytest.mark.asyncio
async def test_get_by_empty_str_id() -> None:
    with pytest.raises(ModelNotFoundError):
        await Company.get_by_id("")


@pytest.mark.asyncio
async def test_missing_collection() -> None:
    class User(AsyncModel):
        name: str
        # normally __collection__ would be defined here

    with pytest.raises(CollectionNotDefined):
        await User(name="John").save()


@pytest.mark.asyncio
async def test_model_aliases() -> None:
    class User(AsyncModel):
        __collection__ = "User"

        first_name: str = Field(..., alias="firstName")
        city: str

    user = User(firstName="John", city="Helsinki")
    await user.save()
    assert user.id

    user_from_db = await User.get_by_id(user.id)
    assert user_from_db.first_name == "John"
    assert user_from_db.city == "Helsinki"


@pytest.mark.asyncio
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
async def test_models_with_valid_custom_id(model_id) -> None:
    product_id = str(uuid4())

    product = Product(product_id=product_id, price=123.45, stock=2)
    product.id = model_id
    await product.save()

    found = await Product.get_by_id(model_id)
    assert found.product_id == product_id

    await found.delete()


@pytest.mark.asyncio
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
async def test_models_with_invalid_custom_id(model_id: str) -> None:
    product = Product(product_id="product 123", price=123.45, stock=2)
    product.id = model_id
    with pytest.raises(InvalidDocumentID):
        await product.save()

    with pytest.raises(ModelNotFoundError):
        await Product.get_by_id(model_id)


@pytest.mark.asyncio
async def test_truncate_collection(create_company) -> None:
    await create_company(company_id="1234567-8")
    await create_company(company_id="1234567-9")

    companies = await Company.find({})
    assert len(companies) == 2

    await Company.truncate_collection()
    new_companies = await Company.find({})
    assert len(new_companies) == 0


@pytest.mark.asyncio
async def test_custom_id_model() -> None:
    c = CustomIDModel(bar="bar")  # type: ignore
    await c.save()

    models = await CustomIDModel.find({})
    assert len(models) == 1

    m = models[0]
    assert m.foo is not None
    assert m.bar == "bar"



@pytest.mark.asyncio
async def test_custom_id_model_get_by_doc_ids() -> None:
    c = CustomIDModel(bar="bar")  # type: ignore
    await c.save()
    assert c.foo

    models = await CustomIDModel.get_by_doc_ids([c.foo, "missing"])
    assert [(m.foo, m.bar) for m in models] == [(c.foo, "bar")]

@pytest.mark.asyncio
async def test_custom_id_conflict() -> None:
    await CustomIDConflictModel(foo="foo", bar="bar").save()

    models = await CustomIDModel.find({})
    assert len(models) == 1

    m = models[0]
    assert m.foo != "foo"
    assert m.bar == "bar"


@pytest.mark.asyncio
async def test_model_id_persistency() -> None:
    c = CustomIDConflictModel(foo="foo", bar="bar")
    await c.save()
    assert c.id

    c = await CustomIDConflictModel.get_by_doc_id(c.id)
    await c.save()

    assert len(await CustomIDConflictModel.find({})) == 1


@pytest.mark.asyncio
async def test_bare_model_document_id_persistency() -> None:
    c = CustomIDModel(bar="bar")  # type: ignore
    await c.save()
    assert c.foo

    c = await CustomIDModel.get_by_doc_id(c.foo)
    await c.save()

    assert len(await CustomIDModel.find({})) == 1


@pytest.mark.asyncio
async def test_bare_model_get_by_empty_doc_id() -> None:
    with pytest.raises(ModelNotFoundError):
        await CustomIDModel.get_by_doc_id("")


@pytest.mark.asyncio
async def test_extra_fields() -> None:
    await CustomIDModelExtra(foo="foo", bar="bar", baz="baz").save()  # type: ignore
    with pytest.raises(ValidationError):
        await CustomIDModel.find({})


@pytest.mark.asyncio
async def test_company_stats(create_company) -> None:
    company: Company = await create_company(company_id="1234567-8")
    company_stats = company.stats()

    stats = await company_stats.get_stats()
    stats.sales = 100
    await stats.save()

    # Ensure the data can be still loaded
    loaded = await company.stats().get_stats()
    assert loaded.sales == stats.sales

    # And that we can still save
    loaded.sales += 1
    await loaded.save()

    stats = await company_stats.get_stats()
    assert stats.sales == 101


@pytest.mark.asyncio
async def test_subcollection_model_safety() -> None:
    """
    Ensure you shouldn't be able to use unprepared subcollection models accidentally
    """
    with pytest.raises(CollectionNotDefined):
        await UserStats.find({})


@pytest.mark.asyncio
async def test_get_user_purchases() -> None:
    u = User(name="Foo")
    await u.save()
    assert u.id

    us = UserStats.model_for(u)
    await us(id="2021", purchases=42).save()

    assert await get_user_purchases(u.id) == 42


@pytest.mark.asyncio
async def test_reload() -> None:
    u = User(name="Foo")
    await u.save()

    # change the value in the database
    u_ = await User.find_one({"name": "Foo"})
    u_.name = "Bar"
    await u_.save()

    assert u.name == "Foo"
    await u.reload()
    assert u.name == "Bar"

    another_user = User(name="Another")
    with pytest.raises(ModelNotFoundError):
        await another_user.reload()


@pytest.mark.asyncio
async def test_save_with_exclude_none() -> None:
    p = Profile(name="Foo")
    await p.save(exclude_none=True)

    document_id = p.get_document_id()
    assert document_id

    # pylint: disable=protected-access
    document = await Profile._get_col_ref().document(document_id).get()

    data = document.to_dict()  # type: ignore[union-attr]
    assert data == {"name": "Foo"}
    await p.save()

    # pylint: disable=protected-access
    document = await Profile._get_col_ref().document(document_id).get()

    data = document.to_dict()  # type: ignore[union-attr]
    assert data == {"name": "Foo", "photo_url": None}


@pytest.mark.asyncio
async def test_save_with_exclude_unset() -> None:
    p = Profile(photo_url=None)
    await p.save(exclude_unset=True)

    document_id = p.get_document_id()
    assert document_id

    # pylint: disable=protected-access
    document = await Profile._get_col_ref().document(document_id).get()

    data = document.to_dict()  # type: ignore[union-attr]
    assert data == {"photo_url": None}
    await p.save()

    # pylint: disable=protected-access
    document = await Profile._get_col_ref().document(document_id).get()

    data = document.to_dict()  # type: ignore[union-attr]
    assert data == {"name": "", "photo_url": None}


@pytest.mark.asyncio
async def test_update_city_in_transaction() -> None:
    """
    Test updating a model in a transaction. Test case from README.
    """

    @async_transactional
    async def decrement_population(transaction: AsyncTransaction, city: City, decrement: int = 1):
        await city.reload(transaction=transaction)
        city.population = max(0, city.population - decrement)
        await city.save(transaction=transaction)

    c = City(id="SF", population=1)
    await c.save()
    await c.increment_population(increment=1)
    assert c.population == 2

    t = get_async_transaction()
    await decrement_population(transaction=t, city=c, decrement=5)
    assert c.population == 0


@pytest.mark.asyncio
async def test_delete_in_transaction(create_company) -> None:
    """
    Test deleting a Company model within a Firestore transaction.
    """
    # Create a company
    company: Company = await create_company(
        company_id="11223344-4", first_name="Joe", last_name="Day"
    )
    _id = company.id
    assert _id

    @async_transactional
    async def delete_company(transaction: AsyncTransaction) -> None:
        await company.delete()

    # Call the transactional function
    t = get_async_transaction()
    await delete_company(t)

    # Outside the transaction, the deletion should now be committed
    with pytest.raises(ModelNotFoundError):
        await Company.get_by_id(_id)


@pytest.mark.asyncio
async def test_delete_model(create_company) -> None:
    company: Company = await create_company(
        company_id="11223344-5", first_name="Jane", last_name="Doe"
    )

    _id = company.id
    assert _id

    await company.delete()

    with pytest.raises(ModelNotFoundError):
        await Company.get_by_id(_id)


@pytest.mark.asyncio
async def test_update_model_in_transaction() -> None:
    """
    Test updating a model in a transaction.
    """

    @async_transactional
    async def update_in_transaction(
        transaction: AsyncTransaction, profile_id: str, name: str
    ) -> None:
        """Updates a Profile in a transaction."""
        profile = Profile(id=profile_id)
        await profile.reload(transaction=transaction)
        profile.name = name
        await profile.save(transaction=transaction)

    p = Profile(name="Foo")
    await p.save()

    t = get_async_transaction()
    await update_in_transaction(t, p.id, name="Bar")
    await p.reload()
    assert p.name == "Bar"


@pytest.mark.asyncio
async def test_update_submodel_in_transaction() -> None:
    """
    Test Updating a submodel in a transaction.
    """

    @async_transactional
    async def update_submodel_in_transaction(
        transaction: AsyncTransaction, user_id: str, period: str
    ) -> UserStats:
        """Updates a UserStats in a transaction."""
        u = await User.get_by_id(user_id, transaction=transaction)
        us = UserStats.model_for(u)
        user_stats: UserStats = await us.get_by_id(period)  # pylint: disable=no-member
        user_stats.purchases += 1
        await user_stats.save(transaction=transaction)
        return user_stats

    u = User(name="Foo")
    await u.save()
    assert u.id
    us = UserStats.model_for(u)
    await us(id="2021", purchases=42).save()  # pylint: disable=no-member

    t = get_async_transaction()
    user_stats = await update_submodel_in_transaction(t, u.id, "2021")
    assert isinstance(user_stats, UserStats)
    assert user_stats.purchases == 43
    assert await get_user_purchases(u.id) == 43


@pytest.mark.asyncio
async def test_model_for_validates_template_values():
    class Org(AsyncModel):
        __collection__ = "orgs"
        slug: str

    class OrgItem(AsyncSubModel):
        id: Optional[str] = None
        value: int = 0

        class Collection(AsyncSubCollection):
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


@pytest.mark.asyncio
async def test_save_with_aliased_document_id():
    class AliasedIdModel(AsyncModel):
        __collection__ = "aliasedIdModels"
        model_config = ConfigDict(populate_by_name=True)

        id: Optional[str] = Field(default=None, alias="docId")
        name: str

    model = AliasedIdModel(name="x")
    await model.save()
    assert model.id

    # The ID is the document name only, not stored under its alias in the data
    snapshot = await AliasedIdModel._get_col_ref().document(model.id).get()
    assert snapshot.to_dict() == {"name": "x"}

    loaded = await AliasedIdModel.get_by_id(model.id)
    assert loaded.id == model.id
    assert loaded.name == "x"
