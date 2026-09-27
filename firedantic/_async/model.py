import re
from abc import ABC
from logging import getLogger
from string import Formatter
from typing import Any, Dict, Iterable, List, Optional, Tuple, Type, TypeVar, Union

import pydantic
from google.cloud.firestore_v1 import (
    AsyncCollectionReference,
    AsyncDocumentReference,
    DocumentSnapshot,
    FieldFilter,
)
from google.cloud.firestore_v1.async_query import AsyncQuery
from google.cloud.firestore_v1.async_transaction import AsyncTransaction
from pydantic import PrivateAttr

import firedantic.operators as op
from firedantic import async_truncate_collection
from firedantic.common import IndexDefinition, OrderDirection
from firedantic.configurations import configuration
from firedantic.exceptions import (
    CollectionNotDefined,
    InvalidDocumentID,
    ModelNotFoundError,
)

TAsyncBareModel = TypeVar("TAsyncBareModel", bound="AsyncBareModel")
TAsyncBareSubModel = TypeVar("TAsyncBareSubModel", bound="AsyncBareSubModel")
logger = getLogger("firedantic")

# https://firebase.google.com/docs/firestore/query-data/queries#query_operators
FIND_TYPES = {
    op.LT,
    op.LTE,
    op.EQ,
    op.NE,
    op.GT,
    op.GTE,
    op.ARRAY_CONTAINS,
    op.ARRAY_CONTAINS_ANY,
    op.IN,
    op.NOT_IN,
}
# FieldPath.document_id(), for filtering and ordering by document path
DOCUMENT_ID = "__name__"
INEQUALITY_TYPES = {op.LT, op.LTE, op.NE, op.GT, op.GTE, op.NOT_IN}


def get_collection_name(cls, collection_name: Optional[str] = None) -> str:
    """
    Return the collection name for `cls`.

    - If `collection_name` is provided, treat it as an explicit collection name
      and prefix it using the configured prefix for the class (via __db_config__).
    - Otherwise, use the class name and the configured prefix.

    :raises CollectionNotDefined: If neither a class collection nor derived name is available.
    """
    # Resolve the config name from the model class (default to "(default)")
    config_name = getattr(cls, "__db_config__", "(default)")

    # If caller provided an explicit collection string, apply prefix from config
    if collection_name:
        cfg = configuration.get_config(config_name)
        prefix = cfg.prefix or ""
        return f"{prefix}{collection_name}"

    if getattr(cls, "__collection__", None):
        cfg = configuration.get_config(config_name)
        return f"{cfg.prefix or ''}{cls.__collection__}"

    raise CollectionNotDefined(f"Missing collection name for {cls.__name__}")


def _get_col_ref(cls, collection_name: Optional[str] = None) -> AsyncCollectionReference:
    """
    Return an AsyncCollectionReference for the model class using the configured async client.

    :param cls: model class
    :param collection_name: optional explicit collection name override
    :raises CollectionNotDefined: when collection cannot be resolved
    """
    # Build the prefixed collection name
    col_name = get_collection_name(cls, collection_name)

    # Resolve config name from class and fetch async client
    config_name = getattr(cls, "__db_config__", "(default)")
    async_client = configuration.get_async_client(config_name)

    # Return the AsyncCollectionReference (this is a real client call)
    col_ref = async_client.collection(col_name)

    # Ensure we got the right object back
    if not hasattr(col_ref, "document"):
        raise RuntimeError(f"_get_col_ref returned unexpected object for {cls}: {type(col_ref)!r}")
    return col_ref


class AsyncBareModel(pydantic.BaseModel, ABC):
    """
    Base model class.

    Implements basic functionality for Pydantic models, such as save, delete, find etc.
    """

    __collection__: Optional[str] = None
    __document_id__: str
    __ttl_field__: Optional[str] = None
    __composite_indexes__: Optional[Iterable[IndexDefinition]] = None
    __db_config__: str = "(default)"  # override in subclasses when needed
    __collection_group__: Optional[str] = None
    __discriminator__: Optional[str] = None

    # Set on models loaded by a collection group query, so they can be saved,
    # reloaded and deleted without knowing their parent document.
    _firedantic_doc_ref: Optional[AsyncDocumentReference] = PrivateAttr(default=None)

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        # Fail at import time instead of on the first collection group query
        cls._get_discriminator_filter()

    async def save(
        self,
        *,
        config_name: Optional[str] = None,
        exclude_unset: bool = False,
        exclude_none: bool = False,
        transaction: Optional[AsyncTransaction] = None,
    ) -> None:
        """
        Saves this model in the database.

        :param exclude_unset: Whether to exclude fields that have not been explicitly set.
        :param exclude_none: Whether to exclude fields that have a value of `None`.
        :param transaction: Optional transaction to use.
        :raise DocumentIDError: If the document ID is not valid.
        """

        # Resolve config to use (explicit -> instance -> class -> default)
        if config_name is not None:
            resolved = config_name
        else:
            resolved = getattr(self, "__db_config__", "")
        if not resolved:
            resolved = getattr(self.__class__, "__db_config__", "(default)")

        # Build payload
        data = self.model_dump(
            by_alias=True, exclude_unset=exclude_unset, exclude_none=exclude_none
        )
        if self.__document_id__ in data:
            del data[self.__document_id__]

        async_client = configuration.get_async_client(resolved)
        if async_client is None:
            raise RuntimeError(f"No async client configured for config '{resolved}'")

        # Models from collection group queries know their own collection; otherwise
        # get collection reference from async_client with the collection_name
        if self._firedantic_doc_ref is not None and config_name is None:
            col_ref = self._firedantic_doc_ref.parent
        else:
            col_ref = async_client.collection(self.get_collection_name())

        # Build doc ref (use provided id if set, otherwise let server generate)
        doc_id = self.get_document_id()
        if doc_id:
            doc_ref = col_ref.document(doc_id)
        else:
            doc_ref = col_ref.document()

        # Use transaction if provided (assume it's compatible) otherwise do direct await set
        if transaction is not None:
            # Transaction.delete/set expects DocumentReference from the same client.
            transaction.set(doc_ref, data)
        else:
            await doc_ref.set(data)

        setattr(self, self.__document_id__, doc_ref.id)

    async def delete(self, transaction: Optional[AsyncTransaction] = None) -> None:
        """
        Deletes this specific model instance from the database.

        :raise DocumentIDError: If the ID is not valid.
        """
        doc_ref = self._get_doc_ref()

        # try to extract client-like objects
        doc_client = getattr(doc_ref, "_client", None) or getattr(doc_ref, "client", None)
        tx_client = (
            getattr(transaction, "_client", None) or getattr(transaction, "_client_async", None)
            if transaction is not None
            else None
        )

        if transaction is not None:
            # Defensive check: make sure the doc_ref is built from same client as the transaction.
            tx_client = getattr(transaction, "_client", None) or getattr(
                transaction, "_client_async", None
            )
            doc_client = getattr(doc_ref, "_client", None) or getattr(doc_ref, "client", None)

            # If both sides expose client objects, ensure they are same identity.
            if tx_client is not None and doc_client is not None and tx_client is not doc_client:
                # Try to rebuild a document reference from the transaction's client using the same path
                path = getattr(doc_ref, "path", None)
                if path is None:
                    raise RuntimeError(
                        "Cannot resolve document path to rebuild doc_ref for transaction."
                    )

                # For most firestores clients, client.document(path) works for sync client;
                # for async, we try client.document(path) as well (it usually exists).
                try:
                    # prefer a method that accepts full path
                    alt_doc_ref = None
                    if hasattr(tx_client, "document"):
                        alt_doc_ref = tx_client.document(path)
                    elif hasattr(tx_client, "collection"):
                        # fallback: split path to collection and doc id
                        parts = path.split("/")
                        if len(parts) >= 2:
                            collection_path = "/".join(parts[:-1])
                            doc_id = parts[-1]
                            alt_doc_ref = tx_client.collection(collection_path).document(doc_id)
                    if alt_doc_ref is None:
                        raise RuntimeError(
                            "Could not rebuild document reference from transaction client."
                        )
                    doc_ref = alt_doc_ref

                except Exception as exc:
                    raise RuntimeError(
                        "Document reference was created from a different Firestore client than "
                        "the provided transaction. Recreate doc_ref from the transaction's client "
                        "or call delete() without a transaction."
                    ) from exc

            # schedule delete on the transaction (this will be committed on transaction commit)
            transaction.delete(doc_ref)
            return

        # no transaction: do a direct delete
        await doc_ref.delete()

    async def reload(self, transaction: Optional[AsyncTransaction] = None) -> None:
        """
        Reloads this model from the database.

        :param transaction: Optional transaction to use.
        :raise ModelNotFoundError: If the document ID is missing in the model.
        """
        doc_id = self.__dict__.get(self.__document_id__)
        if doc_id is None:
            raise ModelNotFoundError("Can not reload unsaved model")

        if self._firedantic_doc_ref is not None:
            updated_model = await self._get_by_doc_ref(self._get_doc_ref(), transaction)
        else:
            updated_model = await self.get_by_doc_id(doc_id, transaction=transaction)
        updated_model_doc_id = updated_model.__dict__[self.__document_id__]
        assert doc_id == updated_model_doc_id

        self.__dict__.update(updated_model.__dict__)

    def get_document_id(self) -> Optional[str]:
        """
        Returns the document ID for this model instance.

        :raise DocumentIDError: If the ID is not valid.
        """
        doc_id = getattr(self, self.__document_id__, None)
        if doc_id is not None:
            self._validate_document_id(doc_id)
        return getattr(self, self.__document_id__, None)

    def get_document_path(self) -> Optional[str]:
        """
        Returns the full document path of this model instance, e.g.
        "animals/abc/surveys/xyz", or None if the model has no document ID yet.
        """
        if self.get_document_id() is None:
            return None
        return str(self._get_doc_ref().path)

    _OrderBy = List[Tuple[str, OrderDirection]]

    @classmethod
    async def delete_all(cls, config_name: Optional[str] = None) -> None:
        """
        Deletes all models of this type from the database.
        """

        # Resolve config to use (explicit -> instance -> class -> default)
        config_name = cls.__db_config__

        client = configuration.get_async_client(config_name)
        col_name = get_collection_name(cls)
        col_ref = client.collection(col_name)

        async for doc in col_ref.stream():
            await doc.reference.delete()

    @classmethod
    async def find(  # pylint: disable=too-many-arguments
        cls: Type[TAsyncBareModel],
        filter_: Optional[Dict[str, Union[str, dict]]] = None,
        order_by: Optional[_OrderBy] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        transaction: Optional[AsyncTransaction] = None,
    ) -> List[TAsyncBareModel]:
        """
        Returns a list of models from the database based on a filter.

        The list can be sorted with the order_by parameter, limits and offets can also be applied.

        Example: `Company.find({"company_id": "1234567-8"})`.
        Example: `Product.find({"stock": {">=": 1}})`.
        Example: `Product.find(order_by=[('unit_value', Query.ASCENDING), ('stock', Query.DESCENDING)], limit=2)`.
        Example: `Product.find({"stock": {">=": 3}}, order_by=[('unit_value', Query.ASCENDING)], limit=2, offset=3)`.

        :param filter_: The filter criteria.
        :param order_by: List of columns and direction to order results by.
        :param limit: Maximum results to return.
        :param offset: Skip the first n results.
        :param transaction: Optional transaction to use.
        :return: List of found models.
        """
        query: Union[AsyncQuery, AsyncCollectionReference] = cls._get_col_ref()
        if filter_:
            for key, value in filter_.items():
                query = cls._add_filter(query, key, value)

        if order_by is not None:
            for field, direction in order_by:
                query = query.order_by(field, direction=direction)  # type: ignore
        if limit is not None:
            query = query.limit(limit)  # type: ignore
        if offset is not None:
            query = query.offset(offset)  # type: ignore

        return [
            cls._model_from_data(doc_id, doc_dict)
            async for doc_id, doc_dict in (
                (doc.id, doc.to_dict())
                async for doc in query.stream(transaction=transaction)  # type: ignore
            )
            if doc_dict is not None
        ]

    @classmethod
    def _model_from_data(
        cls: Type[TAsyncBareModel], doc_id: str, data: Dict[str, Any]
    ) -> TAsyncBareModel:
        if cls.__document_id__ in data:
            logger.warning(
                "%s document ID %s contains conflicting %s in data with value %s",
                cls.__name__,
                doc_id,
                cls.__document_id__,
                data[cls.__document_id__],
            )
        data[cls.__document_id__] = doc_id
        model = cls(**data)
        setattr(model, cls.__document_id__, doc_id)
        return model

    @classmethod
    async def find_in_group(  # pylint: disable=too-many-arguments
        cls: Type[TAsyncBareModel],
        filter_: Optional[Dict[str, Union[str, dict]]] = None,
        order_by: Optional[_OrderBy] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        start_after: Union["AsyncBareModel", str, DocumentSnapshot, None] = None,
        transaction: Optional[AsyncTransaction] = None,
    ) -> List[TAsyncBareModel]:
        """
        Returns a list of models from all collections in the model's collection group.

        Works like `find()`, but runs a collection group query, so for a sub-model with
        `__collection_tpl__ = "animals/{animal_id}/surveys"` it searches the surveys of
        every animal. Only documents below the model's top-level collection (including
        the configured prefix) are queried, and if `__discriminator__` is set, only
        documents whose discriminator field equals the field's default. Any remaining
        documents whose path does not match the collection template, such as
        "animals/abc/visits/xyz/surveys/...", are skipped with a warning, and more
        documents are fetched to fill the page, so a page shorter than `limit` always
        means there are no more results. `offset` counts skipped documents too.

        The returned models remember their document path, so `save()`, `reload()` and
        `delete()` work on them directly.

        Example: `AnimalSurvey.find_in_group({"status": "open"}, limit=20)`.
        Example: `AnimalSurvey.find_in_group(limit=20, start_after=previous_page[-1])`.

        :param filter_: The filter criteria.
        :param order_by: List of columns and direction to order results by.
        :param limit: Maximum results to return.
        :param offset: Skip the first n results.
        :param start_after: Cursor to continue from: a model returned by a previous
            call, its `get_document_path()`, or a document snapshot.
        :param transaction: Optional transaction to use.
        :return: List of found models.
        """
        client = configuration.get_async_client(cls.__db_config__)
        query: AsyncQuery = client.collection_group(cls.get_collection_group_id())

        discriminator = cls._get_discriminator_filter()
        if discriminator is not None:
            query = cls._add_filter(query, *discriminator)  # type: ignore
        if filter_:
            for key, value in filter_.items():
                query = cls._add_filter(query, key, value)  # type: ignore

        if order_by is not None:
            for field, direction in order_by:
                query = query.order_by(field, direction=direction)  # type: ignore

        path_range = cls._get_collection_group_path_range()
        if path_range is not None:
            # Only search below the model's top-level collection (with its prefix), so
            # same-named subcollections elsewhere don't use up the limit
            lower, upper = (client.document(*bound) for bound in path_range)
            query = query.where(filter=FieldFilter(DOCUMENT_ID, ">=", lower))
            query = query.where(filter=FieldFilter(DOCUMENT_ID, "<", upper))
            # The Firestore client adds the implicit orderings for cursors itself, but
            # adds __name__ once per filter on it, which the server rejects
            ordered = {field for field, _ in order_by or []}
            direction = order_by[-1][1] if order_by else "ASCENDING"
            for field in sorted(cls._get_inequality_fields(filter_) - ordered):
                query = query.order_by(field, direction=direction)  # type: ignore
            query = query.order_by(DOCUMENT_ID, direction=direction)  # type: ignore

        cursor = None
        if start_after is not None:
            cursor = await cls._get_cursor_snapshot(start_after, transaction)

        path_pattern = cls._get_collection_group_path_pattern()
        models: List[TAsyncBareModel] = []
        first_round = True
        while True:
            page_query = query
            if cursor is not None:
                page_query = page_query.start_after(cursor)
            requested = None if limit is None else limit - len(models)
            if requested is not None:
                page_query = page_query.limit(requested)  # type: ignore
            # Later rounds continue from the cursor, which is already past the offset
            if offset is not None and first_round:
                page_query = page_query.offset(offset)  # type: ignore
            first_round = False

            fetched = skipped = 0
            async for doc in page_query.stream(transaction=transaction):  # type: ignore
                fetched += 1
                cursor = doc
                if not path_pattern.match(doc.reference.path):
                    logger.warning(
                        "Skipping %s in collection group query for %s: path does not match %s",
                        doc.reference.path,
                        cls.__name__,
                        path_pattern.pattern,
                    )
                    skipped += 1
                    continue
                data = doc.to_dict()
                if data is None:
                    continue
                model = cls._model_from_data(doc.id, data)
                model._firedantic_doc_ref = doc.reference  # type: ignore
                models.append(model)

            # Fetch more only when skipped documents left the page short and the
            # query may still have more results
            if requested is None or skipped == 0 or fetched < requested:
                return models

    @classmethod
    async def find_one_in_group(
        cls: Type[TAsyncBareModel],
        filter_: Optional[Dict[str, Union[str, dict]]] = None,
        order_by: Optional[_OrderBy] = None,
        transaction: Optional[AsyncTransaction] = None,
    ) -> TAsyncBareModel:
        """
        Returns one model from the model's collection group based on a filter.

        :param filter_: The filter criteria.
        :param order_by: List of columns and direction to order results by.
        :return: The model instance.
        :raise ModelNotFoundError: If the entry is not found.
        """
        models = await cls.find_in_group(
            filter_, limit=1, order_by=order_by, transaction=transaction
        )
        try:
            return models[0]
        except IndexError as e:
            raise ModelNotFoundError(f"No '{cls.__name__}' found") from e

    @classmethod
    async def _get_cursor_snapshot(
        cls,
        cursor: Union["AsyncBareModel", str, DocumentSnapshot],
        transaction: Optional[AsyncTransaction] = None,
    ) -> DocumentSnapshot:
        if isinstance(cursor, DocumentSnapshot):
            return cursor
        if isinstance(cursor, AsyncBareModel):
            doc_ref = cursor._get_doc_ref()
        else:
            client = configuration.get_async_client(cls.__db_config__)
            doc_ref = client.document(cursor)
        snapshot = await doc_ref.get(transaction=transaction)
        if not snapshot.exists:
            raise ModelNotFoundError(f"Cursor document '{doc_ref.path}' does not exist")
        return snapshot

    @classmethod
    def _get_collection_path_template(cls) -> str:
        """
        Returns the collection path, with any placeholders, that this model lives in.
        """
        if not cls.__collection__:
            raise CollectionNotDefined(f"Missing collection name for {cls.__name__}")
        return cls.__collection__

    @classmethod
    def get_collection_group_id(cls) -> str:
        """
        Returns the collection group ID used by collection group queries and indexes,
        which is the last segment of the (prefixed) collection path.
        """
        if cls.__collection_group__:
            return cls.__collection_group__
        path = get_collection_name(cls, cls._get_collection_path_template())
        return path.rsplit("/", 1)[-1]

    @classmethod
    def _get_collection_group_path_pattern(cls) -> "re.Pattern[str]":
        """
        Returns a regex matching the document paths this model can have, e.g.
        "animals/{animal_id}/surveys" matches "animals/abc/surveys/xyz".
        """
        path = get_collection_name(cls, cls._get_collection_path_template())
        pattern = ""
        for literal, field_name, _, _ in Formatter().parse(path):
            pattern += re.escape(literal)
            if field_name is not None:
                pattern += "[^/]+"
        return re.compile(f"^{pattern}/[^/]+$")

    @classmethod
    def _get_collection_group_path_range(cls) -> Optional[Tuple[Tuple[str, str], Tuple[str, str]]]:
        """
        Returns (lower, upper) document path bounds covering every document below the
        model's top-level collection, or None for a top-level model.
        """
        path = get_collection_name(cls, cls._get_collection_path_template())
        if "/" not in path:
            return None
        root = path.split("/", 1)[0]
        if "{" in root:
            return None
        # "\x00" sorts before any document ID, and "root\x00" is the first
        # collection ID after "root"
        return (root, "\x00"), (root + "\x00", "\x00")

    @staticmethod
    def _get_inequality_fields(filter_: Optional[Dict[str, Union[str, dict]]]) -> set:
        return {
            field
            for field, value in (filter_ or {}).items()
            if isinstance(value, dict) and INEQUALITY_TYPES.intersection(value)
        }

    @classmethod
    def _get_discriminator_filter(cls) -> Optional[Tuple[str, Any]]:
        """
        Returns the (field, value) filter for the model's `__discriminator__` field.
        """
        if cls.__discriminator__ is None:
            return None
        field = cls.model_fields.get(cls.__discriminator__)
        value = None
        if field is not None and not field.is_required():
            value = field.get_default(call_default_factory=True)
        if field is None or value is None:
            raise ValueError(
                f"{cls.__name__}.__discriminator__ must name a field with a default value, "
                f"got '{cls.__discriminator__}'"
            )
        return field.alias or cls.__discriminator__, value

    @classmethod
    def _add_filter(
        cls, query: Union[AsyncQuery, AsyncCollectionReference], field: str, value: Any
    ) -> Union[AsyncQuery, AsyncCollectionReference]:
        if isinstance(value, dict):
            for f_type in value:
                if f_type not in FIND_TYPES:
                    raise ValueError(
                        f"Unsupported filter type: {f_type}. Supported types are: {', '.join(FIND_TYPES)}"
                    )
                _filter = FieldFilter(field, f_type, value[f_type])
                query: AsyncQuery = query.where(filter=_filter)  # type: ignore
            return query
        else:
            _filter = FieldFilter(field, "==", value)
            query: AsyncQuery = query.where(filter=_filter)  # type: ignore
            return query

    @classmethod
    async def find_one(
        cls: Type[TAsyncBareModel],
        filter_: Optional[Dict[str, Union[str, dict]]] = None,
        order_by: Optional[_OrderBy] = None,
        transaction: Optional[AsyncTransaction] = None,
    ) -> TAsyncBareModel:
        """
        Returns one model from the DB based on a filter.

        :param filter_: The filter criteria.
        :param order_by: List of columns and direction to order results by.
        :return: The model instance.
        :raise ModelNotFoundError: If the entry is not found.
        """
        model = await cls.find(filter_, limit=1, order_by=order_by, transaction=transaction)
        try:
            return model[0]
        except IndexError as e:
            raise ModelNotFoundError(f"No '{cls.__name__}' found") from e

    @classmethod
    async def get_by_doc_id(
        cls: Type[TAsyncBareModel],
        doc_id: str,
        transaction: Optional[AsyncTransaction] = None,
    ) -> TAsyncBareModel:
        """
        Returns a model based on the document ID.

        :param doc_id: The document ID of the entry.
        :param transaction: Optional transaction to use.
        :return: The model.
        :raise ModelNotFoundError: Raised if no matching document is found.
        """

        try:
            cls._validate_document_id(doc_id)
        except InvalidDocumentID as e:
            # Getting a document with doc_id set to an empty string would raise a
            # google.api_core.exceptions.InvalidArgument exception and a doc_id
            # containing an uneven number of slashes would raise a
            # ValueError("A document must have an even number of path elements") and
            # could even load data from a sub collection instead of the desired one.
            raise ModelNotFoundError(
                f"No '{cls.__name__}' found with {cls.__document_id__} '{doc_id}'"
            ) from e

        doc_ref = cls._get_col_ref().document(doc_id)
        return await cls._get_by_doc_ref(doc_ref, transaction)  # type: ignore

    @classmethod
    async def _get_by_doc_ref(
        cls: Type[TAsyncBareModel],
        doc_ref: AsyncDocumentReference,
        transaction: Optional[AsyncTransaction] = None,
    ) -> TAsyncBareModel:
        document: DocumentSnapshot = await doc_ref.get(  # type: ignore[assignment]
            transaction=transaction
        )
        data = document.to_dict()
        if data is None:
            raise ModelNotFoundError(
                f"No '{cls.__name__}' found with {cls.__document_id__} '{doc_ref.id}'"
            )
        data[cls.__document_id__] = doc_ref.id
        model = cls(**data)
        setattr(model, cls.__document_id__, doc_ref.id)
        return model

    @classmethod
    async def truncate_collection(cls, batch_size: int = 128) -> int:
        """
        Removes all documents inside a collection.

        :param batch_size: Batch size for listing documents.
        :return: Number of removed documents.
        """
        return await async_truncate_collection(
            col_ref=cls._get_col_ref(),
            batch_size=batch_size,
        )

    @classmethod
    def _get_col_ref(cls, collection_name: Optional[str] = None) -> AsyncCollectionReference:
        """
        Returns the collection reference.
        """
        return _get_col_ref(cls, collection_name)

    @classmethod
    def get_collection_name(cls) -> str:
        """
        Returns the collection name.
        """
        return get_collection_name(cls, cls.__collection__)

    def _get_doc_ref(self, config_name: Optional[str] = "(default)") -> AsyncDocumentReference:
        """
        Returns the document reference.

        :raise DocumentIDError: If the ID is not valid.
        """
        if self._firedantic_doc_ref is not None:
            return self._firedantic_doc_ref.parent.document(self.get_document_id())  # type: ignore
        return self._get_col_ref().document(self.get_document_id())  # type: ignore

    @staticmethod
    def _validate_document_id(document_id: str):
        """
        Validates the Document ID is valid.

        Based on information from https://firebase.google.com/docs/firestore/quotas#limits

        :raise DocumentIDError: If the ID is not valid.
        """
        if len(document_id.encode("utf-8")) > 1500:
            raise InvalidDocumentID("Document ID must be no longer than 1,500 bytes")

        if "/" in document_id:
            raise InvalidDocumentID("Document ID cannot contain a forward slash (/)")

        if document_id.startswith("__") and document_id.endswith("__") and len(document_id) >= 4:
            raise InvalidDocumentID("Document ID cannot match the regular expression __.*__")

        if document_id in (".", ".."):
            raise InvalidDocumentID(
                "Document ID cannot solely consist of a single period (.) or double periods (..)"
            )

        if document_id == "":
            raise InvalidDocumentID("Document ID cannot be an empty string")


class AsyncModel(AsyncBareModel):
    __document_id__: str = "id"
    id: Optional[str] = None

    @classmethod
    async def get_by_id(
        cls: Type[TAsyncBareModel],
        id_: str,
        transaction: Optional[AsyncTransaction] = None,
    ) -> TAsyncBareModel:
        """
        Get single model by document ID.

        :param id_: Document ID.
        :param transaction: Optional transaction to use.
        :raises ModelNotFoundError: If no model was found by given id.
        """
        return await cls.get_by_doc_id(id_, transaction=transaction)


class AsyncBareSubCollection(ABC):
    __collection_tpl__: Optional[str] = None
    __document_id__: str

    @classmethod
    def model_for(cls, parent, model_class):
        """
        Returns the model for this subcollection.
        """
        parent_props = parent.model_dump(by_alias=True)
        template = cls.__collection_tpl__
        if not template:
            raise CollectionNotDefined(f"Missing __collection_tpl__ for {cls.__name__}")

        # Every placeholder becomes a document ID in the collection path, so it must
        # be a valid one: a value containing "/" would silently point the model at a
        # different document's subcollection
        formatter = Formatter()
        for _, field_name, _, _ in formatter.parse(template):
            if field_name is None:
                continue
            value, _ = formatter.get_field(field_name, (), parent_props)
            try:
                if value is None:
                    raise InvalidDocumentID("Document ID cannot be None")
                AsyncBareModel._validate_document_id(str(value))
            except InvalidDocumentID as e:
                raise InvalidDocumentID(
                    f"Invalid value {value!r} for '{{{field_name}}}' in "
                    f"{cls.__name__}.__collection_tpl__ '{template}': {e}"
                ) from e

        name = model_class.__name__
        ic = type(name, (model_class,), {})
        ic.__collection_cls__ = cls
        ic.__collection__ = template.format(**parent_props)
        ic.__document_id__ = cls.__document_id__
        # A subcollection lives under its parent document, so it must use the
        # parent's database config; any __db_config__ on model_class is ignored.
        ic.__db_config__ = parent.__db_config__

        return ic


class AsyncBareSubModel(AsyncBareModel, ABC):
    __collection_cls__: "AsyncBareSubCollection"
    __collection__: Optional[str] = None
    __document_id__: str

    class Collection(AsyncBareSubCollection, ABC):
        pass

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        # model_for() copies this from the Collection class, but collection group
        # queries use the unbound model class, so it needs to be available there too
        collection_document_id = getattr(cls.Collection, "__document_id__", None)
        if collection_document_id and "__document_id__" not in cls.__dict__:
            cls.__document_id__ = collection_document_id

    @classmethod
    def _create(cls: Type[TAsyncBareSubModel], **kwargs) -> TAsyncBareSubModel:
        return cls(  # type: ignore
            **kwargs,
        )

    @classmethod
    def _get_col_ref(cls, collection_name: Optional[str] = None) -> AsyncCollectionReference:
        """
        Returns the collection reference.
        """
        if cls.__collection__ is None or "{" in cls.__collection__:
            raise CollectionNotDefined(
                f"{cls.__name__} is not properly prepared. "
                f"You should use {cls.__name__}.model_for(parent)"
            )
        return _get_col_ref(cls, cls.__collection__)

    @classmethod
    def _get_collection_path_template(cls) -> str:
        collection_cls = getattr(cls, "__collection_cls__", None) or cls.Collection
        if not collection_cls.__collection_tpl__:
            raise CollectionNotDefined(f"Missing __collection_tpl__ for {cls.__name__}")
        return collection_cls.__collection_tpl__

    def get_parent_id(self) -> Optional[str]:
        """
        Returns the ID of the document this model's subcollection lives under, e.g.
        the animal ID for a model loaded from "animals/abc/surveys/xyz".
        """
        parent = self._get_doc_ref().parent.parent
        return parent.id if parent is not None else None

    @classmethod
    def model_for(cls, parent):
        """
        Returns the model for this submodel.
        """
        return cls.Collection.model_for(parent, cls)


class AsyncSubModel(AsyncBareSubModel):
    id: Optional[str] = None

    @classmethod
    async def get_by_id(
        cls: Type[TAsyncBareModel],
        id_: str,
        transaction: Optional[AsyncTransaction] = None,
    ) -> TAsyncBareModel:
        """
        Get single item by document ID

        :param id_: Document ID.
        :param transaction: Optional transaction to use.
        :raises ModelNotFoundError:
        """
        return await cls.get_by_doc_id(id_, transaction=transaction)


class AsyncSubCollection(AsyncBareSubCollection, ABC):
    __document_id__ = "id"
    __model_cls__: Type[AsyncSubModel]
