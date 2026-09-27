from typing import Any, Dict, Optional

from google.api_core.exceptions import FailedPrecondition


class ModelError(Exception):
    """Generic model error class."""


class InvalidDocumentID(ModelError):
    """Raised when a document ID is invalid."""


class ModelNotFoundError(ModelError):
    """Raised when a model is not found."""


class CollectionNotDefined(ModelError):
    """Raised when the model collection is not defined."""


class MissingIndexError(FailedPrecondition):
    """
    Raised when Firestore rejects a query because an index it needs is missing.

    It's a `FailedPrecondition`, like the error Firestore raises, so existing error
    handling still catches it. The message shows the missing index as a firedantic
    declaration, where firedantic can express it, and as a `firestore.indexes.json`
    entry.

    :ivar collection_group: Collection group ID the index is for.
    :ivar index_json: The `indexes` or `fieldOverrides` entry for `firestore.indexes.json`.
    :ivar is_field_override: Whether `index_json` is a `fieldOverrides` entry.
    :ivar declaration: The firedantic declaration of the index, or None.
    :ivar url: The Firebase console link for creating the index.
    """

    def __init__(
        self,
        message: str,
        *,
        collection_group: str,
        index_json: Dict[str, Any],
        is_field_override: bool,
        declaration: Optional[str],
        url: str,
    ) -> None:
        super().__init__(message)
        self.collection_group = collection_group
        self.index_json = index_json
        self.is_field_override = is_field_override
        self.declaration = declaration
        self.url = url
