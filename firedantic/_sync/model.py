import re
from abc import ABC
from logging import getLogger
from string import Formatter
from typing import (
    Any,
    Dict,
    Iterable,
    List,
    Optional,
    Tuple,
    Type,
    TypeVar,
    Union,
    overload,
)

import pydantic
from google.cloud.firestore_v1 import (
    And,
    CollectionReference,
    DocumentReference,
    DocumentSnapshot,
    FieldFilter,
    Increment,
    Or,
    WriteBatch,
    base_query,
)
from google.cloud.firestore_v1.base_query import BaseQuery
from google.cloud.firestore_v1.transaction import Transaction
from pydantic import PrivateAttr

import firedantic.operators as op
from firedantic import truncate_collection
from firedantic.common import (
    IndexDefinition,
    OrderDirection,
    get_path_value,
    increment_locally,
    is_transform,
    quote_field_names,
    set_path_value,
)
from firedantic.configurations import configuration
from firedantic.exceptions import (
    CollectionNotDefined,
    InvalidDocumentID,
    ModelNotFoundError,
)

TBareModel = TypeVar("TBareModel", bound="BareModel")
TBareSubModel = TypeVar("TBareSubModel", bound="BareSubModel")
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
COMPOSITE_TYPES = {op.OR, op.AND}


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


def _get_col_ref(cls, collection_name: Optional[str] = None) -> CollectionReference:
    """
    Return an CollectionReference for the model class using the configured client.

    :param cls: model class
    :param collection_name: optional explicit collection name override
    :raises CollectionNotDefined: when collection cannot be resolved
    """
    # Build the prefixed collection name
    col_name = get_collection_name(cls, collection_name)

    # Resolve config name from class and fetch client
    config_name = getattr(cls, "__db_config__", "(default)")
    client = configuration.get_client(config_name)

    # Return the CollectionReference (this is a real client call)
    col_ref = client.collection(col_name)

    # Ensure we got the right object back
    if not hasattr(col_ref, "document"):
        raise RuntimeError(f"_get_col_ref returned unexpected object for {cls}: {type(col_ref)!r}")
    return col_ref


def get_writer(
    transaction: Optional[Transaction], batch: Optional[WriteBatch]
) -> Optional[WriteBatch]:
    """
    Returns the transaction or batch to add a write to, or None to write directly.
    Transactions are write batches too, with the same methods for adding writes.

    :raise ValueError: If both a transaction and a batch are given.
    """
    if transaction is not None and batch is not None:
        raise ValueError("Pass either a transaction or a batch, not both")
    return transaction if transaction is not None else batch


class BareModel(pydantic.BaseModel, ABC):
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
    _firedantic_doc_ref: Optional[DocumentReference] = PrivateAttr(default=None)

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        # Fail at import time instead of on the first collection group query
        cls._get_discriminator_filter()

    def save(
        self,
        *,
        config_name: Optional[str] = None,
        exclude_unset: bool = False,
        exclude_none: bool = False,
        merge: bool = False,
        transaction: Optional[Transaction] = None,
        batch: Optional[WriteBatch] = None,
    ) -> None:
        """
        Saves this model in the database.

        By default the stored document is replaced, so fields left out with
        `exclude_unset` or `exclude_none` are removed from it. With `merge=True` only
        the saved fields are written and other stored fields are kept.

        :param config_name: Configuration to use instead of the model's `__db_config__`.
        :param exclude_unset: Whether to exclude fields that have not been explicitly set.
        :param exclude_none: Whether to exclude fields that have a value of `None`.
        :param merge: Whether to merge the fields into the stored document instead of
            replacing it.
        :param transaction: Optional transaction to use.
        :param batch: Optional write batch to add the write to.
        :raise DocumentIDError: If the document ID is not valid.
        """
        writer = get_writer(transaction, batch)
        doc_ref, data = self._prepare_write(config_name, exclude_unset, exclude_none)

        # The transaction or batch must come from the same client as the model
        if writer is not None:
            writer.set(doc_ref, data, merge=merge)
        else:
            doc_ref.set(data, merge=merge)

        setattr(self, self.__document_id__, doc_ref.id)

    def create(
        self,
        *,
        config_name: Optional[str] = None,
        exclude_unset: bool = False,
        exclude_none: bool = False,
        transaction: Optional[Transaction] = None,
        batch: Optional[WriteBatch] = None,
    ) -> None:
        """
        Saves this model in the database as a new document. Unlike `save()`, this fails
        if a document with the same ID already exists. Models without an ID get a
        generated one.

        :param config_name: Configuration to use instead of the model's `__db_config__`.
        :param exclude_unset: Whether to exclude fields that have not been explicitly set.
        :param exclude_none: Whether to exclude fields that have a value of `None`.
        :param transaction: Optional transaction to use. The check for an existing
            document happens when the transaction commits.
        :param batch: Optional write batch to add the write to. The check for an
            existing document happens when the batch commits.
        :raise DocumentIDError: If the document ID is not valid.
        :raise google.api_core.exceptions.AlreadyExists: If the document already exists.
        """
        writer = get_writer(transaction, batch)
        doc_ref, data = self._prepare_write(config_name, exclude_unset, exclude_none)

        if writer is not None:
            writer.create(doc_ref, data)
        else:
            doc_ref.create(data)

        setattr(self, self.__document_id__, doc_ref.id)

    @overload
    def update(
        self,
        *fields: str,
        transaction: Optional[Transaction] = None,
        batch: Optional[WriteBatch] = None,
    ) -> None: ...

    @overload
    def update(
        self,
        changes: Dict[str, Any],
        /,
        *,
        transaction: Optional[Transaction] = None,
        batch: Optional[WriteBatch] = None,
    ) -> None: ...

    def update(
        self,
        *fields: Union[str, Dict[str, Any]],
        transaction: Optional[Transaction] = None,
        batch: Optional[WriteBatch] = None,
    ) -> None:
        """
        Updates fields of the stored document, leaving its other fields as they are.
        Unlike `save(merge=True)`, this fails if the document doesn't exist.

        Called with model field names, it writes the current values of those fields,
        each as a whole. Without arguments, all fields of the model are written.

        Called with a dict, it works like Firestore's `update()`: the keys are
        Firestore field paths, so they use field aliases and dots for nested fields.
        The changes are validated before they are written, and applied to this model
        instance after they are written. Firestore transforms such as `DELETE_FIELD`,
        `SERVER_TIMESTAMP` and `ArrayUnion` are written as they are, but only
        `Increment` is applied to the instance, so use `reload()` to see the others.
        In a transaction or batch the instance is left unchanged, since the write only
        happens when the transaction or batch commits.

        Examples: `product.stock = 5; product.update("stock")` and
        `counter.update({"stats.visits": 3, "totalCount": Increment(1)})`.

        :param fields: Names of the model fields to write, or a dict of changes.
        :param transaction: Optional transaction to use.
        :param batch: Optional write batch to add the write to.
        :raise ModelNotFoundError: If the model has not been saved.
        :raise ValueError: If a field is not a field of the model.
        :raise pydantic.ValidationError: If the changes are not valid for the model.
        :raise google.api_core.exceptions.NotFound: If the document does not exist.
        """
        writer = get_writer(transaction, batch)
        if self.__dict__.get(self.__document_id__) is None:
            raise ModelNotFoundError("Can not update unsaved model")

        updated_model = None
        if fields and isinstance(fields[0], dict):
            if len(fields) > 1:
                raise TypeError("update() takes field names or one dict of changes, not both")
            changes = fields[0]
            data, updated_model = self._prepare_changes(changes)
        else:
            if not all(isinstance(field, str) for field in fields):
                raise TypeError("update() takes field names or one dict of changes, not both")
            data = self._prepare_field_update(fields)  # type: ignore[arg-type]

        doc_ref = self._get_doc_ref()
        if writer is not None:
            writer.update(doc_ref, data)
            return
        doc_ref.update(data)

        if updated_model is not None:
            self.__dict__.update(updated_model.__dict__)
            for path, value in changes.items():
                if isinstance(value, Increment):
                    increment_locally(self, path, value.value)

    def _prepare_field_update(self, fields: Tuple[str, ...]) -> Dict[str, Any]:
        """
        Returns the data for writing the current values of `fields` with `update()`.
        """
        unknown = set(fields) - set(type(self).model_fields)
        if unknown:
            raise ValueError(f"Unknown fields for {type(self).__name__}: {sorted(unknown)}")

        data = self.model_dump(by_alias=True, include=set(fields) or None)
        data.pop(self._get_document_id_key(), None)
        return quote_field_names(data)

    def _prepare_changes(
        self: TBareModel, changes: Dict[str, Any]
    ) -> Tuple[Dict[str, Any], TBareModel]:
        """
        Validates the changes for `update()` by applying them to this model's data.
        Returns the data to write, with the values serialized like `save()` does, and
        the model with the changes applied, not counting Firestore transforms.
        """
        doc_id = self.__dict__[self.__document_id__]
        stored = self.model_dump(by_alias=True)
        stored.pop(self._get_document_id_key(), None)
        values = {path: value for path, value in changes.items() if not is_transform(value)}
        for path, value in values.items():
            set_path_value(stored, path, value)
        updated_model = self._model_from_data(doc_id, stored)

        dumped = updated_model.model_dump(by_alias=True)
        data = dict(changes)
        for path in values:
            found, value = get_path_value(dumped, path)
            # Paths the model doesn't store, e.g. ignored extra fields, are written as given
            if found:
                data[path] = value
        return data, updated_model

    def _prepare_write(
        self, config_name: Optional[str], exclude_unset: bool, exclude_none: bool
    ) -> Tuple[DocumentReference, Dict[str, Any]]:
        """
        Returns the document reference and data for writing this model with `save()`
        or `create()`. The reference has a generated ID if the model has none.
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
        # The ID is the document name, not part of the data; drop it by the key
        # model_dump() used for it, which is its alias if it has one
        data.pop(self._get_document_id_key(), None)

        client = configuration.get_client(resolved)
        if client is None:
            raise RuntimeError(f"No client configured for config '{resolved}'")

        # Models from collection group queries know their own collection; otherwise
        # get collection reference from client with the collection_name
        if self._firedantic_doc_ref is not None and config_name is None:
            col_ref = self._firedantic_doc_ref.parent
        else:
            col_ref = client.collection(self.get_collection_name())

        # Build doc ref (use provided id if set, otherwise let server generate)
        doc_id = self.get_document_id()
        if doc_id:
            doc_ref = col_ref.document(doc_id)
        else:
            doc_ref = col_ref.document()
        return doc_ref, data

    def increment(
        self,
        field: str,
        amount: Union[int, float] = 1,
        transaction: Optional[Transaction] = None,
        *,
        batch: Optional[WriteBatch] = None,
    ) -> None:
        """
        Atomically increments a numeric field of this model in the database.

        `field` is a Firestore field path, so it uses field aliases and dots for nested
        fields, e.g. "stock" or "stats.visits". If the stored value is missing or not a
        number, Firestore sets it to `amount`. The same change is applied to this model
        instance, but other writes to the field aren't, so use `reload()` to get the
        stored value. In a transaction or batch the instance is left unchanged, since
        the write only happens when the transaction or batch commits.

        Example: `product.increment("stock", -1)`.

        :param field: Firestore field path to increment.
        :param amount: Amount to add. Use a negative value to decrement.
        :param transaction: Optional transaction to use.
        :param batch: Optional write batch to add the write to.
        :raise ModelNotFoundError: If the model has not been saved.
        :raise google.api_core.exceptions.NotFound: If the document does not exist.
        """
        writer = get_writer(transaction, batch)
        if self.__dict__.get(self.__document_id__) is None:
            raise ModelNotFoundError("Can not increment unsaved model")

        doc_ref = self._get_doc_ref()
        data = {field: Increment(amount)}
        if writer is not None:
            writer.update(doc_ref, data)
            return
        doc_ref.update(data)
        increment_locally(self, field, amount)

    def delete(
        self,
        transaction: Optional[Transaction] = None,
        *,
        batch: Optional[WriteBatch] = None,
        recursive: bool = False,
    ) -> Optional[int]:
        """
        Deletes this specific model instance from the database.

        Firestore doesn't delete subcollections with their parent document. With
        `recursive=True` the documents in all subcollections below this one are deleted
        too, in batches. That is not atomic, so it can't be used in a transaction or
        batch.

        :param transaction: Optional transaction to use.
        :param batch: Optional write batch to add the write to.
        :param recursive: Also delete all documents in subcollections below this one.
        :return: Number of deleted documents with `recursive=True`, otherwise None.
        :raise DocumentIDError: If the ID is not valid.
        :raise ValueError: If `recursive=True` is used with a transaction or batch.
        """
        writer = get_writer(transaction, batch)
        doc_ref = self._get_doc_ref()
        if recursive:
            if writer is not None:
                raise ValueError("Recursive delete can not be used in a transaction or batch")
            client = configuration.get_client(self.__db_config__)
            return client.recursive_delete(doc_ref)
        if writer is not None:
            # Like save(): the transaction or batch must come from the same client
            writer.delete(doc_ref)
            return None
        doc_ref.delete()
        return None

    def reload(self, transaction: Optional[Transaction] = None) -> None:
        """
        Reloads this model from the database.

        :param transaction: Optional transaction to use.
        :raise ModelNotFoundError: If the document ID is missing in the model.
        """
        doc_id = self.__dict__.get(self.__document_id__)
        if doc_id is None:
            raise ModelNotFoundError("Can not reload unsaved model")

        if self._firedantic_doc_ref is not None:
            updated_model = self._get_by_doc_ref(self._get_doc_ref(), transaction)
        else:
            updated_model = self.get_by_doc_id(doc_id, transaction=transaction)
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

    @classmethod
    def _get_document_id_key(cls) -> str:
        """
        Returns the key under which `model_dump(by_alias=True)` puts the document ID.
        """
        field = cls.model_fields.get(cls.__document_id__)
        if field is None:
            return cls.__document_id__
        return field.serialization_alias or field.alias or cls.__document_id__

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
    def delete_all(cls, config_name: Optional[str] = None) -> None:
        """
        Deletes all models of this type from the database, in batches.

        :param config_name: Configuration to use instead of the model's `__db_config__`.
        """
        client = configuration.get_client(config_name or cls.__db_config__)
        col_ref = client.collection(get_collection_name(cls))
        truncate_collection(col_ref)

    @classmethod
    def find(  # pylint: disable=too-many-arguments
        cls: Type[TBareModel],
        filter_: Optional[Dict[str, Any]] = None,
        order_by: Optional[_OrderBy] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        start_after: Union["BareModel", str, DocumentSnapshot, None] = None,
        transaction: Optional[Transaction] = None,
    ) -> List[TBareModel]:
        """
        Returns a list of models from the database based on a filter.

        The list can be sorted with the order_by parameter, limits and offets can also be applied.

        Example: `Company.find({"company_id": "1234567-8"})`.
        Example: `Product.find({"stock": {">=": 1}})`.
        Example: `Product.find(order_by=[('unit_value', Query.ASCENDING), ('stock', Query.DESCENDING)], limit=2)`.
        Example: `Product.find({"stock": {">=": 3}}, order_by=[('unit_value', Query.ASCENDING)], limit=2, offset=3)`.
        Example: `Product.find(order_by=[('stock', Query.ASCENDING)], limit=20, start_after=previous_page[-1])`.

        :param filter_: The filter criteria.
        :param order_by: List of columns and direction to order results by.
        :param limit: Maximum results to return.
        :param offset: Skip the first n results.
        :param start_after: Cursor to continue from: a model returned by a previous
            call, its document ID or `get_document_path()`, or a document snapshot.
        :param transaction: Optional transaction to use.
        :return: List of found models.
        """
        query = cls._get_query(filter_)

        if order_by is not None:
            for field, direction in order_by:
                query = query.order_by(field, direction=direction)  # type: ignore
        if limit is not None:
            query = query.limit(limit)  # type: ignore
        if offset is not None:
            query = query.offset(offset)  # type: ignore
        if start_after is not None:
            if isinstance(start_after, str) and "/" not in start_after:
                start_after = cls._get_col_ref().document(start_after).path
            cursor = cls._get_cursor_snapshot(start_after, transaction)
            query = query.start_after(cursor)  # type: ignore

        return [
            cls._model_from_data(doc_id, doc_dict)
            for doc_id, doc_dict in (
                (doc.id, doc.to_dict())
                for doc in query.stream(transaction=transaction)  # type: ignore
            )
            if doc_dict is not None
        ]

    @classmethod
    def count(
        cls,
        filter_: Optional[Dict[str, Any]] = None,
        transaction: Optional[Transaction] = None,
    ) -> int:
        """
        Returns the number of models matching a filter, using a count aggregation
        query, so the documents themselves are not read.

        Example: `Product.count({"stock": {">=": 1}})`.

        :param filter_: The filter criteria.
        :param transaction: Optional transaction to use.
        :return: Number of matching models.
        """
        query = cls._get_query(filter_)
        return int(cls._get_aggregate(query.count(), transaction))

    @classmethod
    def sum(
        cls,
        field: str,
        filter_: Optional[Dict[str, Any]] = None,
        transaction: Optional[Transaction] = None,
    ) -> Union[int, float]:
        """
        Returns the sum of a numeric field over the models matching a filter, using a
        sum aggregation query, so the documents themselves are not read.

        `field` is a Firestore field path, so it uses field aliases and dots for nested
        fields. Values that aren't numbers are ignored, and the sum of no values is 0.

        Example: `Product.sum("stock", {"price": {">=": 10}})`.

        :param field: Firestore field path to sum.
        :param filter_: The filter criteria.
        :param transaction: Optional transaction to use.
        :return: Sum of the field.
        """
        query = cls._get_query(filter_)
        total: Union[int, float] = cls._get_aggregate(query.sum(field), transaction)
        return total

    @classmethod
    def avg(
        cls,
        field: str,
        filter_: Optional[Dict[str, Any]] = None,
        transaction: Optional[Transaction] = None,
    ) -> Optional[float]:
        """
        Returns the average of a numeric field over the models matching a filter, using
        an average aggregation query, so the documents themselves are not read.

        `field` is a Firestore field path, so it uses field aliases and dots for nested
        fields. Values that aren't numbers are ignored. The average is None if no
        matching model has the field, and 0.0 if the field has no numbers, because
        the Firestore client library reads Firestore's null result as 0.0.

        Example: `Product.avg("price", {"stock": {">=": 1}})`.

        :param field: Firestore field path to average.
        :param filter_: The filter criteria.
        :param transaction: Optional transaction to use.
        :return: Average of the field, or None if no matching model has the field.
        """
        return cls._get_average(cls._get_query(filter_), field, transaction)

    @classmethod
    def _get_query(cls, filter_: Optional[Dict[str, Any]]) -> Union[BaseQuery, CollectionReference]:
        """
        Returns the query for the model's collection with `filter_` applied.
        """
        query: Union[BaseQuery, CollectionReference] = cls._get_col_ref()
        if filter_:
            for key, value in filter_.items():
                query = cls._add_filter(query, key, value)
        return query

    @staticmethod
    def _get_aggregate(aggregation_query: Any, transaction: Optional[Transaction]) -> Any:
        """
        Runs an aggregation query with a single aggregation and returns its value.
        """
        results = aggregation_query.get(transaction=transaction)
        # Sync stubs type the result as a flat list, but both return one list per query
        return results[0][0].value

    @staticmethod
    def _get_average(
        query: Union[BaseQuery, CollectionReference],
        field: str,
        transaction: Optional[Transaction],
    ) -> Optional[float]:
        """
        Returns the average of `field` over the documents of `query`, or None if none
        of them has the field.
        """
        # The client decodes a null average as 0.0, so count the documents to tell an
        # empty result apart. Like the average, the count only includes documents
        # that have the field.
        aggregation_query = query.count(alias="count").avg(field, alias="avg")  # type: ignore
        results = aggregation_query.get(transaction=transaction)
        values = {result.alias: result.value for result in results[0]}
        if not values["count"]:
            return None
        return float(values["avg"])

    @classmethod
    def _model_from_data(cls: Type[TBareModel], doc_id: str, data: Dict[str, Any]) -> TBareModel:
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
    def find_in_group(  # pylint: disable=too-many-arguments
        cls: Type[TBareModel],
        filter_: Optional[Dict[str, Any]] = None,
        order_by: Optional[_OrderBy] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        start_after: Union["BareModel", str, DocumentSnapshot, None] = None,
        transaction: Optional[Transaction] = None,
    ) -> List[TBareModel]:
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
        query = cls._get_group_query(filter_)

        if order_by is not None:
            for field, direction in order_by:
                query = query.order_by(field, direction=direction)  # type: ignore

        if cls._get_collection_group_path_range() is not None:
            # The Firestore client adds the implicit orderings for cursors itself, but
            # adds __name__ once per filter on it, which the server rejects
            ordered = {field for field, _ in order_by or []}
            direction = order_by[-1][1] if order_by else "ASCENDING"
            for field in sorted(cls._get_inequality_fields(filter_) - ordered):
                query = query.order_by(field, direction=direction)  # type: ignore
            query = query.order_by(DOCUMENT_ID, direction=direction)  # type: ignore

        cursor = None
        if start_after is not None:
            cursor = cls._get_cursor_snapshot(start_after, transaction)

        path_pattern = cls._get_collection_group_path_pattern()
        models: List[TBareModel] = []
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
            for doc in page_query.stream(transaction=transaction):  # type: ignore
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
    def count_in_group(
        cls,
        filter_: Optional[Dict[str, Any]] = None,
        transaction: Optional[Transaction] = None,
    ) -> int:
        """
        Returns the number of models matching a filter in the model's collection group,
        using a count aggregation query, so the documents themselves are not read.

        Counts the same documents as `find_in_group()`, except that the count can't
        check document paths, so it includes documents that `find_in_group()` would
        skip for not matching the collection template, such as
        "animals/abc/visits/xyz/surveys/...". Use `__discriminator__` to exclude them.

        Example: `AnimalSurvey.count_in_group({"status": "open"})`.

        :param filter_: The filter criteria.
        :param transaction: Optional transaction to use.
        :return: Number of matching models.
        """
        query = cls._get_group_query(filter_)
        return int(cls._get_aggregate(query.count(), transaction))

    @classmethod
    def sum_in_group(
        cls,
        field: str,
        filter_: Optional[Dict[str, Any]] = None,
        transaction: Optional[Transaction] = None,
    ) -> Union[int, float]:
        """
        Returns the sum of a numeric field over the models matching a filter in the
        model's collection group, using a sum aggregation query. Works like `sum()`,
        and like `count_in_group()` it includes documents whose path doesn't match the
        collection template.

        Example: `AnimalSurvey.sum_in_group("score", {"status": "open"})`.

        :param field: Firestore field path to sum.
        :param filter_: The filter criteria.
        :param transaction: Optional transaction to use.
        :return: Sum of the field.
        """
        query = cls._get_group_aggregation_query(filter_, field)
        total: Union[int, float] = cls._get_aggregate(query.sum(field), transaction)
        return total

    @classmethod
    def avg_in_group(
        cls,
        field: str,
        filter_: Optional[Dict[str, Any]] = None,
        transaction: Optional[Transaction] = None,
    ) -> Optional[float]:
        """
        Returns the average of a numeric field over the models matching a filter in the
        model's collection group, using an average aggregation query. Works like
        `avg()`, and like `count_in_group()` it includes documents whose path doesn't
        match the collection template.

        Example: `AnimalSurvey.avg_in_group("score", {"status": "open"})`.

        :param field: Firestore field path to average.
        :param filter_: The filter criteria.
        :param transaction: Optional transaction to use.
        :return: Average of the field, or None if no matching model has the field.
        """
        query = cls._get_group_aggregation_query(filter_, field)
        return cls._get_average(query, field, transaction)

    @classmethod
    def _get_group_aggregation_query(
        cls, filter_: Optional[Dict[str, Any]], field: str
    ) -> BaseQuery:
        """
        Returns the collection group query for a sum or average of `field`.
        """
        # Firestore rejects a sum or average with the path range unless the query is
        # ordered by the field. Ordering only skips documents without the field, which
        # the aggregation skips anyway.
        return cls._get_group_query(filter_).order_by(field)

    @classmethod
    def _get_group_query(cls, filter_: Optional[Dict[str, Any]]) -> BaseQuery:
        """
        Returns the collection group query for the model, with the discriminator,
        `filter_` and the bounds of the model's top-level collection applied.
        """
        client = configuration.get_client(cls.__db_config__)
        query: BaseQuery = client.collection_group(cls.get_collection_group_id())

        discriminator = cls._get_discriminator_filter()
        if discriminator is not None:
            query = cls._add_filter(query, *discriminator)  # type: ignore
        if filter_:
            for key, value in filter_.items():
                query = cls._add_filter(query, key, value)  # type: ignore

        path_range = cls._get_collection_group_path_range()
        if path_range is not None:
            # Only search below the model's top-level collection (with its prefix), so
            # same-named subcollections elsewhere don't use up the limit or the count
            lower, upper = (client.document(*bound) for bound in path_range)
            query = query.where(filter=FieldFilter(DOCUMENT_ID, ">=", lower))
            query = query.where(filter=FieldFilter(DOCUMENT_ID, "<", upper))
        return query

    @classmethod
    def find_one_in_group(
        cls: Type[TBareModel],
        filter_: Optional[Dict[str, Any]] = None,
        order_by: Optional[_OrderBy] = None,
        transaction: Optional[Transaction] = None,
    ) -> TBareModel:
        """
        Returns one model from the model's collection group based on a filter.

        :param filter_: The filter criteria.
        :param order_by: List of columns and direction to order results by.
        :return: The model instance.
        :raise ModelNotFoundError: If the entry is not found.
        """
        models = cls.find_in_group(filter_, limit=1, order_by=order_by, transaction=transaction)
        try:
            return models[0]
        except IndexError as e:
            raise ModelNotFoundError(f"No '{cls.__name__}' found") from e

    @classmethod
    def _get_cursor_snapshot(
        cls,
        cursor: Union["BareModel", str, DocumentSnapshot],
        transaction: Optional[Transaction] = None,
    ) -> DocumentSnapshot:
        if isinstance(cursor, DocumentSnapshot):
            return cursor
        if isinstance(cursor, BareModel):
            doc_ref = cursor._get_doc_ref()
        else:
            client = configuration.get_client(cls.__db_config__)
            doc_ref = client.document(cursor)
        snapshot = doc_ref.get(transaction=transaction)
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

    @classmethod
    def _get_inequality_fields(cls, filter_: Optional[Dict[str, Any]]) -> set:
        fields = set()
        for field, value in (filter_ or {}).items():
            if field in COMPOSITE_TYPES:
                for clause in value:
                    fields |= cls._get_inequality_fields(clause)
            elif isinstance(value, dict) and INEQUALITY_TYPES.intersection(value):
                fields.add(field)
        return fields

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
        cls, query: Union[BaseQuery, CollectionReference], field: str, value: Any
    ) -> Union[BaseQuery, CollectionReference]:
        for _filter in cls._get_field_filters(field, value):
            query = query.where(filter=_filter)  # type: ignore
        return query

    @classmethod
    def _get_field_filters(cls, field: str, value: Any) -> List[base_query.BaseFilter]:
        """
        Returns the Firestore filters for one key of a filter dict. `op.OR` and `op.AND`
        take a list of filter dicts, whose keys are combined with AND.
        """
        if field in COMPOSITE_TYPES:
            if not isinstance(value, list) or not value:
                raise ValueError(f"{field} takes a non-empty list of filter dicts")
            clauses: List[base_query.BaseFilter] = []
            for clause in value:
                if not isinstance(clause, dict) or not clause:
                    raise ValueError(f"{field} takes a non-empty list of filter dicts")
                filters = [f for key, v in clause.items() for f in cls._get_field_filters(key, v)]
                clauses.append(filters[0] if len(filters) == 1 else And(filters))
            return [Or(clauses) if field == op.OR else And(clauses)]
        if isinstance(value, dict):
            for f_type in value:
                if f_type not in FIND_TYPES:
                    raise ValueError(
                        f"Unsupported filter type: {f_type}. Supported types are: {', '.join(FIND_TYPES)}"
                    )
            return [FieldFilter(field, f_type, f_value) for f_type, f_value in value.items()]
        return [FieldFilter(field, "==", value)]

    @classmethod
    def find_one(
        cls: Type[TBareModel],
        filter_: Optional[Dict[str, Any]] = None,
        order_by: Optional[_OrderBy] = None,
        transaction: Optional[Transaction] = None,
    ) -> TBareModel:
        """
        Returns one model from the DB based on a filter.

        :param filter_: The filter criteria.
        :param order_by: List of columns and direction to order results by.
        :return: The model instance.
        :raise ModelNotFoundError: If the entry is not found.
        """
        model = cls.find(filter_, limit=1, order_by=order_by, transaction=transaction)
        try:
            return model[0]
        except IndexError as e:
            raise ModelNotFoundError(f"No '{cls.__name__}' found") from e

    @classmethod
    def get_by_doc_id(
        cls: Type[TBareModel],
        doc_id: str,
        transaction: Optional[Transaction] = None,
    ) -> TBareModel:
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
        return cls._get_by_doc_ref(doc_ref, transaction)  # type: ignore

    @classmethod
    def get_by_doc_ids(
        cls: Type[TBareModel],
        doc_ids: Iterable[str],
        transaction: Optional[Transaction] = None,
    ) -> List[TBareModel]:
        """
        Returns the models with the given document IDs, fetched in one request.

        The models are returned in the order of `doc_ids`. Documents that don't exist
        are left out, and each document is returned only once.

        :param doc_ids: The document IDs of the entries.
        :param transaction: Optional transaction to use.
        :return: List of found models.
        :raise ModelNotFoundError: If a document ID is not valid, like `get_by_doc_id()`.
        """
        unique_ids = list(dict.fromkeys(doc_ids))
        for doc_id in unique_ids:
            try:
                cls._validate_document_id(doc_id)
            except InvalidDocumentID as e:
                raise ModelNotFoundError(
                    f"No '{cls.__name__}' found with {cls.__document_id__} '{doc_id}'"
                ) from e
        if not unique_ids:
            return []

        col_ref = cls._get_col_ref()
        client = configuration.get_client(cls.__db_config__)
        doc_refs = [col_ref.document(doc_id) for doc_id in unique_ids]
        found = {}
        for doc in client.get_all(doc_refs, transaction=transaction):
            data = doc.to_dict()
            if data is not None:
                found[doc.id] = cls._model_from_data(doc.id, data)
        return [found[doc_id] for doc_id in unique_ids if doc_id in found]

    @classmethod
    def _get_by_doc_ref(
        cls: Type[TBareModel],
        doc_ref: DocumentReference,
        transaction: Optional[Transaction] = None,
    ) -> TBareModel:
        document: DocumentSnapshot = doc_ref.get(  # type: ignore[assignment]
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
    def truncate_collection(cls, batch_size: int = 128) -> int:
        """
        Removes all documents inside a collection.

        :param batch_size: Batch size for listing documents.
        :return: Number of removed documents.
        """
        return truncate_collection(
            col_ref=cls._get_col_ref(),
            batch_size=batch_size,
        )

    @classmethod
    def _get_col_ref(cls, collection_name: Optional[str] = None) -> CollectionReference:
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

    def _get_doc_ref(self) -> DocumentReference:
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


class Model(BareModel):
    __document_id__: str = "id"
    id: Optional[str] = None

    @classmethod
    def get_by_id(
        cls: Type[TBareModel],
        id_: str,
        transaction: Optional[Transaction] = None,
    ) -> TBareModel:
        """
        Get single model by document ID.

        :param id_: Document ID.
        :param transaction: Optional transaction to use.
        :raises ModelNotFoundError: If no model was found by given id.
        """
        return cls.get_by_doc_id(id_, transaction=transaction)

    @classmethod
    def get_by_ids(
        cls: Type[TBareModel],
        ids: Iterable[str],
        transaction: Optional[Transaction] = None,
    ) -> List[TBareModel]:
        """
        Get models by document IDs in one request, like `get_by_doc_ids()`.

        :param ids: Document IDs.
        :param transaction: Optional transaction to use.
        :raises ModelNotFoundError: If an ID is not valid.
        """
        return cls.get_by_doc_ids(ids, transaction=transaction)


class BareSubCollection(ABC):
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
                BareModel._validate_document_id(str(value))
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


class BareSubModel(BareModel, ABC):
    __collection_cls__: "BareSubCollection"
    __collection__: Optional[str] = None
    __document_id__: str

    class Collection(BareSubCollection, ABC):
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
    def _create(cls: Type[TBareSubModel], **kwargs) -> TBareSubModel:
        return cls(  # type: ignore
            **kwargs,
        )

    @classmethod
    def _get_col_ref(cls, collection_name: Optional[str] = None) -> CollectionReference:
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


class SubModel(BareSubModel):
    id: Optional[str] = None

    @classmethod
    def get_by_id(
        cls: Type[TBareSubModel],
        id_: str,
        transaction: Optional[Transaction] = None,
    ) -> TBareSubModel:
        """
        Get single item by document ID

        :param id_: Document ID.
        :param transaction: Optional transaction to use.
        :raises ModelNotFoundError:
        """
        return cls.get_by_doc_id(id_, transaction=transaction)

    @classmethod
    def get_by_ids(
        cls: Type[TBareSubModel],
        ids: Iterable[str],
        transaction: Optional[Transaction] = None,
    ) -> List[TBareSubModel]:
        """
        Get items by document IDs in one request, like `get_by_doc_ids()`.

        :param ids: Document IDs.
        :param transaction: Optional transaction to use.
        :raises ModelNotFoundError: If an ID is not valid.
        """
        return cls.get_by_doc_ids(ids, transaction=transaction)


class SubCollection(BareSubCollection, ABC):
    __document_id__ = "id"
    __model_cls__: Type[SubModel]
