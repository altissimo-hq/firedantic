from google.cloud.firestore_v1 import CollectionReference

# Firestore's limit on writes per batch
MAX_BATCH_WRITES = 500


def truncate_collection(col_ref: CollectionReference, batch_size: int = 128) -> int:
    """
    Removes all documents inside a collection, using batched writes.

    Subcollections of the removed documents are left in place, as Firestore does not
    delete them with their parent.

    :param col_ref: A collection reference to the collection to be truncated.
    :param batch_size: Number of documents to list and delete per round.
    :return: Number of removed documents.
    """
    client = col_ref._client
    count = 0

    while True:
        batch = client.batch()
        pending = deleted = 0
        for doc in col_ref.limit(batch_size).stream():  # type: ignore
            batch.delete(doc.reference)
            pending += 1
            deleted += 1
            if pending == MAX_BATCH_WRITES:
                batch.commit()
                batch = client.batch()
                pending = 0
        if pending:
            batch.commit()

        count += deleted
        if deleted < batch_size:
            return count
