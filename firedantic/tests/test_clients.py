import asyncio
from os import environ
from unittest.mock import Mock

import google.auth.credentials
import pytest
from google.cloud.firestore import AsyncClient, Client

import firedantic.configurations as cfg_module
from firedantic import Configuration, configure
from firedantic.exceptions import CollectionNotDefined


# --- tiny fake client classes to capture construction args ---
class FakeClient:
    def __init__(self, *args, **kwargs):
        self._constructed_args = args
        self._constructed_kwargs = kwargs


class FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        self._constructed_args = args
        self._constructed_kwargs = kwargs


# --- Tests ---------------------------------------------------------
def test_get_client_lazy_creation_and_caching(monkeypatch):
    """
    get_client() should create a Client lazily (on first call) and then cache it.
    """
    monkeypatch.setattr(cfg_module, "Client", FakeClient)

    cfg = Configuration()
    # register a config with a project so that lazy creation is allowed
    item = cfg.add(name="x", project="proj-x", prefix="px-")

    # no client yet
    assert item.client is None

    # first call -> construct and cache
    client1 = cfg.get_client("x")
    assert isinstance(client1, FakeClient)
    assert cfg.get_config("x").client is client1

    # subsequent call -> same instance
    client2 = cfg.get_client("x")
    assert client2 is client1

    # verify constructor received our project in kwargs (if implementation provides it)
    # accept either 'project' in kwargs or stored on item.project
    assert cfg.get_config("x").project == "proj-x"


def test_get_async_client_lazy_creation_and_caching(monkeypatch):
    """
    get_async_client() should create an AsyncClient lazily and cache it.
    """
    monkeypatch.setattr(cfg_module, "AsyncClient", FakeAsyncClient)

    cfg = Configuration()
    item = cfg.add(name="y", project="proj-y", prefix="py-")

    assert item.async_client is None

    async_client1 = cfg.get_async_client("y")
    assert isinstance(async_client1, FakeAsyncClient)
    assert cfg.get_config("y").async_client is async_client1

    async_client2 = cfg.get_async_client("y")
    assert async_client2 is async_client1


def test_preserve_prebuilt_clients():
    """
    If user supplies a prebuilt client / async_client to add(), ensure it uses those.
    """
    cfg = Configuration()

    prebuilt_sync = FakeClient()
    prebuilt_async = FakeAsyncClient()

    cfg.add(
        name="prebuilt",
        project="proj-pre",
        prefix="pp-",
        client=prebuilt_sync,
        async_client=prebuilt_async,
    )

    # get_client/get_async_client should return the exact instances given
    got_sync = cfg.get_client("prebuilt")
    got_async = cfg.get_async_client("prebuilt")
    assert got_sync is prebuilt_sync
    assert got_async is prebuilt_async
    assert cfg.get_config("prebuilt").client is prebuilt_sync
    assert cfg.get_config("prebuilt").async_client is prebuilt_async


def test_get_client_no_project_raises(monkeypatch):
    """
    If no project was supplied and no client was given, get_client() should raise a clear error.
    """
    # ensure environment does NOT provide a project fallback
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)

    # add a config without project and without client
    cfg = Configuration()
    cfg.add(name="noproj", project=None, prefix="np-")

    with pytest.raises(RuntimeError):
        cfg.get_client("noproj")

    with pytest.raises(RuntimeError):
        cfg.get_async_client("noproj")


def test_multiple_clients_are_independent(monkeypatch):
    """
    Ensure default and billing configs return distinct client objects and preserve stored metadata.
    """
    monkeypatch.setattr(cfg_module, "Client", FakeClient)
    monkeypatch.setattr(cfg_module, "AsyncClient", FakeAsyncClient)
    creds = Mock(spec=google.auth.credentials.Credentials)

    cfg = Configuration()
    cfg.add(prefix="firedantic-test-", project="proj-default", credentials=creds)
    cfg.add(name="billing", prefix="billing-", project="proj-billing", credentials=creds)

    # clients for "(default)"" config
    default_sync = cfg.get_client()
    default_async = cfg.get_async_client()
    assert isinstance(default_sync, FakeClient)
    assert isinstance(default_async, FakeAsyncClient)

    # clients for billing config
    billing_sync = cfg.get_client("billing")
    billing_async = cfg.get_async_client("billing")
    assert isinstance(billing_sync, FakeClient)
    assert isinstance(billing_async, FakeAsyncClient)

    # Ensure the four objects are distinct (sync vs async and default vs billing)
    assert default_sync is not billing_sync
    assert default_async is not billing_async
    assert default_sync is not default_async
    assert billing_sync is not billing_async

    # verify metadata preserved
    assert cfg.get_config().prefix == "firedantic-test-"
    assert cfg.get_config().project == "proj-default"
    assert cfg.get_config("billing").prefix == "billing-"
    assert cfg.get_config("billing").project == "proj-billing"


def test___getitem___and_contains_behavior(monkeypatch):
    """
    Ensure dict-like accessors work: __getitem__ and __contains__.
    """
    monkeypatch.setattr(cfg_module, "Client", FakeClient)
    cfg = Configuration()
    cfg.add(name="abc", project="p-abc", prefix="pa-")
    assert "abc" in cfg
    assert "(default)" in cfg
    assert cfg["abc"].project == "p-abc"
    assert cfg["(default)"].prefix == ""


def test_configure_client():
    project = "firedantic-test"
    prefix = "firedantic-test-"
    creds = Mock(spec=google.auth.credentials.Credentials)

    if not environ.get("FIRESTORE_EMULATOR_HOST"):
        raise Exception("Firestore emulator must be running")

    config = Configuration()
    config.add(prefix=prefix, project=project, credentials=creds)

    assert config["(default)"].project == project
    assert config["(default)"].prefix == prefix
    client = config.get_client()
    assert isinstance(client, Client)


### Test sync/async client classes ###


def test_configure_async_client():
    # Firestore emulator must be running if using locally.
    project = "firedantic-test"
    prefix = "firedantic-test-"
    creds = Mock(spec=google.auth.credentials.Credentials)

    if environ.get("FIRESTORE_EMULATOR_HOST"):
        client = AsyncClient(
            project=project,
            credentials=creds,
        )
    else:
        raise Exception("Firestore emulator must be running")

    config = Configuration()
    config.add(prefix=prefix, project=project, credentials=creds)

    assert config["(default)"].project == project
    assert config["(default)"].prefix == prefix
    asyncClient = config.get_async_client()
    assert isinstance(asyncClient, AsyncClient)


def test_configure_multiple_clients():
    config = Configuration()
    mock_creds = Mock(spec=google.auth.credentials.Credentials)

    # name = (default)
    config.add(prefix="firedantic-test-", project="firedantic-test", credentials=mock_creds)

    # name = billing
    config.add(
        name="billing",
        prefix="test-billing-",
        project="test-billing",
        credentials=mock_creds,
    )

    # assert that all 4 clients are unique
    client = config.get_client()
    assert isinstance(client, Client)

    asyncClient = config.get_async_client()
    assert isinstance(asyncClient, AsyncClient)

    billingClient = config.get_client("billing")
    assert isinstance(client, Client)

    billingAsyncClient = config.get_async_client("billing")
    assert isinstance(asyncClient, AsyncClient)

    assert client != asyncClient != billingClient != billingAsyncClient

    # assert that CONFIGURATIONS holds two different client configs
    assert config["(default)"].prefix == "firedantic-test-"
    assert config["(default)"].project == "firedantic-test"
    assert config["(default)"].credentials == mock_creds

    assert config["billing"].prefix == "test-billing-"
    assert config["billing"].project == "test-billing"
    assert config["billing"].credentials == mock_creds


### Test admin client classes ###


# helper fake admin client classes to capture construction arguments
class FakeAdminClient:
    def __init__(self, *args, **kwargs):
        self._constructed_args = args
        self._constructed_kwargs = kwargs


class FakeAsyncAdminClient:
    def __init__(self, *args, **kwargs):
        self._constructed_args = args
        self._constructed_kwargs = kwargs


def test_get_admin_client_lazy_creation(monkeypatch):
    """
    get_admin_client() should create a FirestoreAdminClient lazily and cache it.
    """
    cfg = Configuration()

    # monkeypatch the admin client class used inside the module
    monkeypatch.setattr("firedantic.configurations.FirestoreAdminClient", FakeAdminClient)

    # add a config that does not supply admin_client upfront
    cfg.add(name="x", project="proj-a", database="(default)", prefix="p-")

    # initially there should be no admin client stored on the ConfigItem
    item = cfg.get_config("x")
    assert item.admin_client is None

    # first call should create it
    admin = cfg.get_admin_client("x")
    assert isinstance(admin, FakeAdminClient)
    assert cfg.get_config("x").admin_client is admin  # cached

    # subsequent call returns same instance (cached)
    admin2 = cfg.get_admin_client("x")
    assert admin2 is admin


def test_get_async_admin_client_lazy_creation(monkeypatch):
    """
    get_async_admin_client() should create an async admin client lazily and cache it.
    """
    cfg = Configuration()

    monkeypatch.setattr("firedantic.configurations.FirestoreAdminAsyncClient", FakeAsyncAdminClient)

    cfg.add(name="y", project="proj-b", database="billing", prefix="pb-")

    item = cfg.get_config("y")
    assert item.async_admin_client is None

    async_admin = cfg.get_async_admin_client("y")
    assert isinstance(async_admin, FakeAsyncAdminClient)
    assert cfg.get_config("y").async_admin_client is async_admin

    # cached
    assert cfg.get_async_admin_client("y") is async_admin


def test_preserve_prebuilt_admin_client():
    """
    If the user supplies an admin_client in add(), the registry should use it verbatim.
    """
    cfg = Configuration()

    # create a prebuilt fake admin client instance
    prebuilt = FakeAdminClient()
    cfg.add(
        name="prebuilt",
        project="proj-pre",
        database="(default)",
        prefix="pp-",
        admin_client=prebuilt,
    )

    # get_admin_client should return the exact object we passed
    got = cfg.get_admin_client("prebuilt")
    assert got is prebuilt
    assert cfg.get_config("prebuilt").admin_client is prebuilt


def test_admin_client_creation_receives_transport_and_client_options(monkeypatch):
    """
    Ensure the admin-client constructor receives client_options and transport passed into add().
    """
    cfg = Configuration()

    captured = {}

    # define a fake constructor that records kwargs
    def fake_constructor(*args, **kwargs):
        inst = FakeAdminClient(*args, **kwargs)
        captured["kwargs"] = kwargs
        captured["args"] = args
        return inst

    monkeypatch.setattr("firedantic.configurations.FirestoreAdminClient", fake_constructor)

    # pass some admin_transport and client_options into add()
    fake_transport = object()
    fake_client_options = {"api_endpoint": "test-endpoint"}
    fake_client_info = Mock()

    cfg.add(
        name="z",
        project="proj-z",
        database="(default)",
        prefix="pz-",
        credentials=None,
        admin_transport=fake_transport,
        client_options=fake_client_options,
        client_info=fake_client_info,
    )

    # creating admin client should call fake_constructor with the supplied values (as kwargs)
    admin = cfg.get_admin_client("z")
    assert isinstance(admin, FakeAdminClient)
    assert "kwargs" in captured
    # check the captured kwargs including transport + client_options and a client_info (or default)
    assert captured["kwargs"].get("transport") is fake_transport
    assert captured["kwargs"].get("client_options") == fake_client_options
    assert "client_info" in captured["kwargs"]


def test_get_admin_client_unknown_config_raises():
    cfg = Configuration()
    with pytest.raises(KeyError):
        cfg.get_admin_client("does-not-exist")


def test_legacy_configure_shim_creates_config(monkeypatch):
    from firedantic.configurations import CONFIGURATIONS
    from firedantic.configurations import Client as RealClient  # monkeypatch below
    from firedantic.configurations import configuration, configure

    # fake client class to avoid network
    class FakeClient:
        pass

    monkeypatch.setattr("firedantic.configurations.Client", FakeClient)

    fake = FakeClient()
    configure(fake, prefix="legacy-")
    cfg = configuration.get_config("(default)")
    assert cfg.prefix == "legacy-"
    assert CONFIGURATIONS["prefix"] == "legacy-"
    assert CONFIGURATIONS["db"] is fake


def test_legacy_configure_warns(monkeypatch):
    import warnings

    from firedantic.configurations import configure

    class FakeClient:
        pass

    with warnings.catch_warnings(record=True) as rec:
        warnings.simplefilter("always")
        configure(FakeClient(), prefix="x")
        assert any(issubclass(w.category, DeprecationWarning) for w in rec)


def test_lazy_async_client_recreated_on_another_event_loop(monkeypatch):
    monkeypatch.setattr(cfg_module, "AsyncClient", FakeAsyncClient)
    cfg = Configuration()
    cfg.add(name="z", project="proj-z")

    async def get():
        return cfg.get_async_client("z")

    async def get_twice():
        return cfg.get_async_client("z"), cfg.get_async_client("z")

    # Cached within one loop, recreated once that loop is gone
    first, again = asyncio.run(get_twice())
    assert first is again
    assert asyncio.run(get()) is not first


def test_provided_async_client_kept_across_event_loops():
    cfg = Configuration()
    provided = FakeAsyncClient()
    cfg.add(name="w", project="proj-w", async_client=provided)

    async def get():
        return cfg.get_async_client("w")

    assert asyncio.run(get()) is provided
    assert asyncio.run(get()) is provided


def test_collection_helpers_follow_model_rules():
    cfg = Configuration()
    client = Mock()
    async_client = Mock()
    cfg.add(name="things", prefix="t-", project="proj", client=client, async_client=async_client)

    class Thing:
        __collection__ = "things"
        __db_config__ = "things"

    class Nameless:
        __collection__ = None

    # The model's own config is used by default, an explicit one wins
    assert cfg.get_collection_name(Thing) == "t-things"
    assert cfg.get_collection_name(Thing, "(default)") == "things"
    assert cfg.get_collection_ref(Thing) is client.collection.return_value
    client.collection.assert_called_once_with("t-things")
    assert cfg.get_async_collection_ref(Thing) is async_client.collection.return_value
    async_client.collection.assert_called_once_with("t-things")

    # No name is derived from the class name
    with pytest.raises(CollectionNotDefined):
        cfg.get_collection_name(Nameless)


def test_batches_use_the_named_config(monkeypatch):
    cfg = Configuration()
    client = Mock()
    async_client = Mock()
    cfg.add(name="things", project="proj", client=client, async_client=async_client)

    assert cfg.get_batch("things") is client.batch.return_value
    assert cfg.get_async_batch("things") is async_client.batch.return_value

    # The module-level helpers forward to the shared configuration
    monkeypatch.setattr(cfg_module, "configuration", cfg)
    assert cfg_module.get_batch("things") is client.batch.return_value
    assert cfg_module.get_async_batch("things") is async_client.batch.return_value
