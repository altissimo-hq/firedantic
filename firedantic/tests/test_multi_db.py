"""
Routing tests for multi-database configurations. These use mocked clients, so no
Firestore emulator is required.
"""

from unittest.mock import MagicMock

import pytest

from firedantic import AsyncModel, AsyncSubCollection, AsyncSubModel, Model, SubCollection, SubModel
from firedantic.configurations import ConfigItem, Configuration, configuration


class Parent(Model):
    __collection__ = "parents"
    __db_config__ = "backup"


class DefaultParent(Model):
    __collection__ = "parents"


class Child(SubModel):
    class Collection(SubCollection):
        __collection_tpl__ = "parents/{id}/children"


class ChildDeclaringBackup(SubModel):
    __db_config__ = "backup"

    class Collection(SubCollection):
        __collection_tpl__ = "parents/{id}/children"


class AsyncParent(AsyncModel):
    __collection__ = "parents"
    __db_config__ = "backup"


class AsyncChild(AsyncSubModel):
    class Collection(AsyncSubCollection):
        __collection_tpl__ = "parents/{id}/children"


@pytest.fixture
def clients(monkeypatch):
    """
    Register mocked "(default)" and "backup" configs on the global configuration.
    """
    clients = {}
    for name, prefix in (("(default)", "d-"), ("backup", "b-")):
        clients[name] = MagicMock(name=f"client-{name}")
        monkeypatch.setitem(
            configuration.config,
            name,
            ConfigItem(
                name=name,
                project="proj",
                database=name,
                prefix=prefix,
                client=clients[name],
                async_client=clients[name],
            ),
        )
    return clients


def test_submodel_uses_parent_database(clients):
    Child.model_for(Parent(id="x"))._get_col_ref()

    clients["backup"].collection.assert_called_once_with("b-parents/x/children")
    clients["(default)"].collection.assert_not_called()


def test_submodel_ignores_own_db_config(clients):
    # The subcollection lives under the parent document, so the parent's database wins
    ChildDeclaringBackup.model_for(DefaultParent(id="x"))._get_col_ref()

    clients["(default)"].collection.assert_called_once_with("d-parents/x/children")
    clients["backup"].collection.assert_not_called()


def test_async_submodel_uses_parent_database(clients):
    AsyncChild.model_for(AsyncParent(id="x"))._get_col_ref()

    clients["backup"].collection.assert_called_once_with("b-parents/x/children")
    clients["(default)"].collection.assert_not_called()


@pytest.mark.parametrize(
    "client_cls, getter",
    [("Client", "get_client"), ("AsyncClient", "get_async_client")],
)
def test_lazy_client_receives_database(monkeypatch, client_cls, getter):
    fake = MagicMock()
    monkeypatch.setattr(f"firedantic.configurations.{client_cls}", fake)

    cfg = Configuration()
    cfg.add(name="backup", project="proj", database="backup-db")
    getattr(cfg, getter)("backup")

    assert fake.call_args.kwargs["database"] == "backup-db"
