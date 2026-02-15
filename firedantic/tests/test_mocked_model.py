from typing import Type
from unittest.mock import AsyncMock, MagicMock

import pytest
from google.cloud.firestore import AsyncClient

from firedantic import AsyncModel, configure
from firedantic.configurations import CONFIGURATIONS


@pytest.fixture
def MockModelClass() -> Type[AsyncModel]:
    class TestModel(AsyncModel):
        __collection__ = "mock_models"
        name: str
        age: int = 30

    return TestModel


@pytest.fixture
def mock_client():
    client = AsyncMock(spec=AsyncClient)
    # Reset configurations to ensure isolation
    CONFIGURATIONS.clear()
    configure(client, prefix="test_")
    return client


@pytest.mark.asyncio
async def test_save_new_model(mock_client, MockModelClass):
    collection_mock = MagicMock()
    document_mock = MagicMock()

    mock_client.collection.return_value = collection_mock
    collection_mock.document.return_value = document_mock

    document_mock.id = "generated-id"
    # Ensure set/update are async
    document_mock.set = AsyncMock()
    document_mock.update = AsyncMock()

    model = MockModelClass(name="Alice")
    await model.save()

    mock_client.collection.assert_called_with("test_mock_models")
    assert collection_mock.document.called
    document_mock.set.assert_called_once()
    assert model.id == "generated-id"


@pytest.mark.asyncio
async def test_save_update_model(mock_client, MockModelClass):
    collection_mock = MagicMock()
    document_mock = MagicMock()

    mock_client.collection.return_value = collection_mock
    collection_mock.document.return_value = document_mock
    document_mock.set = AsyncMock()
    document_mock.update = AsyncMock()

    model = MockModelClass(id="existing-id", name="Bob")
    await model.save()

    mock_client.collection.assert_called_with("test_mock_models")
    collection_mock.document.assert_called_with("existing-id")
    # It might use update or set.
    assert document_mock.update.called or document_mock.set.called


@pytest.mark.asyncio
async def test_delete_model(mock_client, MockModelClass):
    collection_mock = MagicMock()
    document_mock = MagicMock()

    mock_client.collection.return_value = collection_mock
    collection_mock.document.return_value = document_mock
    document_mock.delete = AsyncMock()

    # Verify we DO NOT call stream (which would indicate bulk delete)
    collection_mock.stream = MagicMock(
        side_effect=Exception("Should not stream collection for single delete!")
    )

    model = MockModelClass(id="to-delete", name="Charlie")
    await model.delete()

    mock_client.collection.assert_called_with("test_mock_models")
    collection_mock.document.assert_called_with("to-delete")
    document_mock.delete.assert_called_once()


@pytest.mark.asyncio
async def test_delete_all(mock_client, MockModelClass):
    collection_mock = MagicMock()
    document_mock = MagicMock()

    mock_client.collection.return_value = collection_mock

    mock_doc = MagicMock()
    mock_doc.reference = document_mock
    document_mock.delete = AsyncMock()

    async def async_stream():
        yield mock_doc
        yield mock_doc

    collection_mock.stream.side_effect = async_stream

    await MockModelClass.delete_all()

    assert collection_mock.stream.called
    assert document_mock.delete.call_count == 2


@pytest.mark.asyncio
async def test_find(mock_client, MockModelClass):
    collection_mock = MagicMock()

    mock_client.collection.return_value = collection_mock

    # Mocking the query chain: .where().where().stream()
    # If find() calls where(), we need to return the request (or new query object) to allow chaining.
    # The final object calls stream().

    # We can use a recursive magic mock for chaining or set return_value to self.
    query_mock = MagicMock()
    collection_mock.where.return_value = query_mock
    query_mock.where.return_value = query_mock  # For multiple wheres
    query_mock.order_by.return_value = query_mock
    query_mock.limit.return_value = query_mock
    query_mock.offset.return_value = query_mock

    # Mock stream to return an async generator
    mock_snapshot = MagicMock()
    mock_snapshot.id = "doc-1"
    mock_snapshot.to_dict.return_value = {"name": "Dave", "age": 40}

    async def async_stream():
        yield mock_snapshot

    query_mock.stream.return_value = async_stream()

    # In case it calls stream() directly on collection (no filters)
    collection_mock.stream.side_effect = async_stream

    # Case 1: find({}) -> no filters, might call stream on collection mock directly or via "select" etc.
    # Assuming firedantic calls collection(...).stream() for empty query,
    # or collection(...).where(..).stream()

    results = await MockModelClass.find({"name": "Dave"})

    assert len(results) == 1
    assert results[0].name == "Dave"
    assert results[0].id == "doc-1"
