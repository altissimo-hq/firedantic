import asyncio
import warnings
from os import environ
from typing import Any, Dict, Optional, Type, Union

from google.auth.credentials import Credentials
from google.cloud.firestore_admin_v1 import FirestoreAdminClient
from google.cloud.firestore_admin_v1.services.firestore_admin import (
    FirestoreAdminAsyncClient,
)
from google.cloud.firestore_admin_v1.services.firestore_admin.transports.base import (
    DEFAULT_CLIENT_INFO,
)
from google.cloud.firestore_v1 import (
    AsyncClient,
    AsyncCollectionReference,
    AsyncTransaction,
    AsyncWriteBatch,
    Client,
    CollectionReference,
    Transaction,
    WriteBatch,
)
from pydantic import BaseModel, Field

from firedantic.exceptions import CollectionNotDefined

# --- Old compatibility surface (kept for backwards compatibility) ---
CONFIGURATIONS: Dict[str, Any] = {}


def configure(db: Union[Client, AsyncClient], prefix: str = "") -> None:
    """
    Legacy helper: updates the module-level `configuration` default entry and preserves
    the old CONFIGURATIONS mapping so old callers continue to work.
    Configures the prefix and DB.
    :param db: The firestore client instance.
    :param prefix: The prefix to use for collection names.
    """

    # soft deprecation notice for users (no stacktrace)
    warnings.warn(
        "firedantic.configure(db, ...) is deprecated and will be removed in a "
        "future release. Use firedantic.configurations.configuration.add(...) instead.",
        DeprecationWarning,
        stacklevel=2,
    )

    if isinstance(db, AsyncClient):
        configuration.add(name="(default)", prefix=prefix, async_client=db)
    else:
        # treat as sync client
        configuration.add(name="(default)", prefix=prefix, client=db)

    CONFIGURATIONS["db"] = db
    CONFIGURATIONS["prefix"] = prefix


def get_transaction() -> Transaction:
    """Backward-compatible sync transaction getter, forwards to new implementation."""
    return configuration.get_transaction()


def get_async_transaction() -> AsyncTransaction:
    """Backward-compatible async transaction getter, forwards to new implementation."""
    return configuration.get_async_transaction()


def get_batch(config_name: Optional[str] = None) -> WriteBatch:
    """
    Returns a new sync write batch for the configuration. Pass it as `batch` to the
    write methods of models, then call `commit()` to write everything at once.
    """
    return configuration.get_batch(config_name)


def get_async_batch(config_name: Optional[str] = None) -> AsyncWriteBatch:
    """
    Returns a new async write batch for the configuration. Pass it as `batch` to the
    write methods of models, then call `await commit()` to write everything at once.
    """
    return configuration.get_async_batch(config_name)


# --- New configuration system ---
class ConfigItem(BaseModel):
    """
    Holds configuration for a named Firestore connection.
    Clients may be provided directly, or created lazily from the stored params.
    """

    name: str
    prefix: str = ""
    project: Optional[str] = None
    database: str = "(default)"

    # client objects (may be None; created lazily)
    client: Optional[Any] = None
    async_client: Optional[Any] = None
    admin_client: Optional[Any] = None
    async_admin_client: Optional[Any] = None

    # creation params (kept so we can lazily instantiate clients)
    credentials: Optional[Credentials] = None
    client_info: Optional[Any] = None
    client_options: Optional[Union[Dict[str, Any], Any]] = None
    admin_transport: Optional[Any] = None

    # Event loops the lazily created async clients are bound to. A client can't be
    # used from another loop, so it's recreated when the loop changes. Clients passed
    # in by the caller have no recorded loop and are never replaced.
    async_client_loop: Optional[Any] = Field(default=None, exclude=True, repr=False)
    async_admin_client_loop: Optional[Any] = Field(default=None, exclude=True, repr=False)

    model_config = {"arbitrary_types_allowed": True}


def _current_loop() -> Optional[asyncio.AbstractEventLoop]:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _bound_to_other_loop(loop: Optional[asyncio.AbstractEventLoop]) -> bool:
    """
    Whether a lazily created async client bound to `loop` can't be used now, because
    that loop is closed or a different loop is running.
    """
    if loop is None:
        return False
    if loop.is_closed():
        return True
    current = _current_loop()
    return current is not None and current is not loop


class Configuration:
    """
    Registry for named Firestore configurations.

    Usage Example:
        configuration.add(name="billing", project="myproj", credentials=creds)
        client = configuration.get_client("billing")
    """

    def __init__(self) -> None:
        # mapping name -> ConfigItem
        self.config: Dict[str, ConfigItem] = {}

        # Create a sensible default config entry, but avoid passing empty string as project.
        default_project = environ.get("GOOGLE_CLOUD_PROJECT") or None

        self.config["(default)"] = ConfigItem(
            name="(default)",
            prefix="",
            project=default_project,
            database="(default)",
        )

    def add(
        self,
        name: str = "(default)",  # adding a config without a name results in overriding the default
        *,
        project: Optional[str] = None,
        database: str = "(default)",
        prefix: str = "",
        client: Optional[Any] = None,
        async_client: Optional[Any] = None,
        admin_client: Optional[Any] = None,
        async_admin_client: Optional[Any] = None,
        credentials: Optional[Credentials] = None,
        client_info: Optional[Any] = None,
        client_options: Optional[Union[Dict[str, Any], Any]] = None,
        admin_transport: Optional[Any] = None,
    ) -> ConfigItem:
        """
        Add a named configuration.

        You may either pass a pre-built client and/or an async_client,
        or provide only project/credentials so clients will be constructed lazily.
        """

        normalized_project = self._normalize_project(project)

        item = ConfigItem(
            name=name,
            project=normalized_project,
            database=database,
            prefix=prefix,
            client=client,
            async_client=async_client,
            admin_client=admin_client,
            async_admin_client=async_admin_client,
            credentials=credentials,
            client_info=client_info,
            client_options=client_options,
            admin_transport=admin_transport,
        )
        self.config[name] = item
        return item

    # dict-like accessors
    def __getitem__(self, name: str) -> ConfigItem:
        return self.get_config(name)

    def __contains__(self, name: str) -> bool:
        return name in self.config

    def get(self, name: str, default=None):
        return self.config.get(name, default)

    def get_config(self, name: str = "(default)") -> ConfigItem:
        try:
            return self.config[name]
        except KeyError as err:
            raise KeyError(
                f"Configuration '{name}' not found. Available: {list(self.config.keys())}"
            ) from err

    def get_config_names(self):
        return list(self.config.keys())

    def _normalize_project(self, project: Optional[str]) -> Optional[str]:
        """
        Convert empty-string project to None so Client(...) doesn't get empty string for project (i.e. project="")
        """
        if project:
            return project
        return environ.get("GOOGLE_CLOUD_PROJECT") or None

    # sync client accessor (lazy-create)
    def get_client(self, name: Optional[str] = None) -> Client:
        resolved = name if name is not None else "(default)"
        cfg = self.get_config(resolved)
        if cfg.client is None:
            # don't try to create a client if we lack project info
            if cfg.project is None:
                raise RuntimeError(
                    f"No sync client configured for '{resolved}' and no project available; "
                    "call configuration.add(..., client=..., project=...) or set "
                    "GOOGLE_CLOUD_PROJECT and let add() build the client."
                )
            cfg.client = Client(
                project=cfg.project,
                credentials=cfg.credentials,
                database=cfg.database,
                client_info=cfg.client_info,
                client_options=cfg.client_options,  # type: ignore[arg-type]
            )
        return cfg.client

    # async client accessor (lazy-create)
    def get_async_client(self, name: Optional[str] = None) -> AsyncClient:
        resolved = name if name is not None else "(default)"
        cfg = self.get_config(resolved)

        if cfg.async_client is not None and _bound_to_other_loop(cfg.async_client_loop):
            cfg.async_client = None
        if cfg.async_client is None:
            if cfg.project is None:
                raise RuntimeError(
                    f"No async client configured for '{resolved}' and no project available; "
                    "call configuration.add(..., async_client=..., project=...) or set "
                    "GOOGLE_CLOUD_PROJECT and let add() build the client."
                )
            cfg.async_client = AsyncClient(
                project=cfg.project,
                credentials=cfg.credentials,
                database=cfg.database,
                client_info=cfg.client_info,
                client_options=cfg.client_options,  # type: ignore[arg-type]
            )
            cfg.async_client_loop = _current_loop()
        return cfg.async_client

    # admin client accessor (lazy-create)
    def get_admin_client(self, name: Optional[str] = None) -> FirestoreAdminClient:
        resolved = name if name is not None else "(default)"
        cfg = self.get_config(resolved)

        if cfg.admin_client is None:
            cfg.admin_client = FirestoreAdminClient(
                credentials=cfg.credentials,
                transport=cfg.admin_transport,
                client_options=cfg.client_options,  # type: ignore[arg-type]
                client_info=cfg.client_info or DEFAULT_CLIENT_INFO,
            )
        return cfg.admin_client

    # async admin client accessor (lazy-create)
    def get_async_admin_client(self, name: Optional[str] = None) -> FirestoreAdminAsyncClient:
        resolved = name if name is not None else "(default)"
        cfg = self.get_config(resolved)

        if cfg.async_admin_client is not None and _bound_to_other_loop(cfg.async_admin_client_loop):
            cfg.async_admin_client = None
        if cfg.async_admin_client is None:
            cfg.async_admin_client = FirestoreAdminAsyncClient(
                credentials=cfg.credentials,
                transport=cfg.admin_transport,
                client_options=cfg.client_options,  # type: ignore[arg-type]
                client_info=cfg.client_info or DEFAULT_CLIENT_INFO,
            )
            cfg.async_admin_client_loop = _current_loop()
        return cfg.async_admin_client

    # transactions
    def get_transaction(self, name: Optional[str] = None) -> Transaction:
        return self.get_client(name=name).transaction()

    def get_async_transaction(self, name: Optional[str] = None) -> AsyncTransaction:
        return self.get_async_client(name=name).transaction()

    # batched writes
    def get_batch(self, name: Optional[str] = None) -> WriteBatch:
        return self.get_client(name=name).batch()

    def get_async_batch(self, name: Optional[str] = None) -> AsyncWriteBatch:
        return self.get_async_client(name=name).batch()

    # helpers for models to derive collection name / reference
    @staticmethod
    def _resolve_model_config(model_class: Type, config_name: Optional[str]) -> str:
        if config_name is not None:
            return config_name
        return str(getattr(model_class, "__db_config__", "(default)"))

    def get_collection_name(self, model_class: Type, config_name: Optional[str] = None) -> str:
        """
        Return the prefixed collection name for `model_class`, using the prefix of
        `config_name` or, by default, of the model's `__db_config__`. Same rules as
        `model_class.get_collection_name()`.

        :raises CollectionNotDefined: If the model has no `__collection__`.
        """
        resolved = self._resolve_model_config(model_class, config_name)
        collection = getattr(model_class, "__collection__", None)
        if not collection:
            raise CollectionNotDefined(f"Missing collection name for {model_class.__name__}")
        return f"{self.get_config(resolved).prefix or ''}{collection}"

    def get_collection_ref(
        self, model_class: Type, name: Optional[str] = None
    ) -> CollectionReference:
        """
        Return a CollectionReference for `model_class` using the (lazily created) sync client.
        """
        resolved = self._resolve_model_config(model_class, name)
        return self.get_client(resolved).collection(self.get_collection_name(model_class, resolved))

    def get_async_collection_ref(
        self, model_class: Type, name: Optional[str] = None
    ) -> AsyncCollectionReference:
        """
        Return an AsyncCollectionReference for `model_class` using the (lazily created)
        async client.
        """
        resolved = self._resolve_model_config(model_class, name)
        return self.get_async_client(resolved).collection(
            self.get_collection_name(model_class, resolved)
        )


# make the module-level singleton available to models/tests
# other modules should: from firedantic.configurations import configuration
configuration = Configuration()
