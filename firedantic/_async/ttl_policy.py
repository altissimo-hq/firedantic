from logging import getLogger
from typing import Iterable, List, Optional, Type

from google.api_core.operation_async import AsyncOperation
from google.cloud.firestore_admin_v1 import Field
from google.cloud.firestore_admin_v1.services.firestore_admin import (
    FirestoreAdminAsyncClient,
)

from firedantic._async.model import AsyncBareModel
from firedantic.configurations import configuration

logger = getLogger("firedantic")


async def set_up_ttl_policies(
    gcloud_project: str,
    models: Iterable[Type[AsyncBareModel]],
    database: Optional[str] = None,
    client: Optional[FirestoreAdminAsyncClient] = None,
) -> List[AsyncOperation]:
    """
    Set up TTL policies for models.

    :param gcloud_project: The technical name of the project in Google Cloud.
    :param models: Models for which to set up the TTL policy.
    :param database: The Firestore database. Defaults to the database of each model's
        configuration.
    :param client: The Firestore admin client.
    :return: List of operations that were launched to enable the policies.
    """
    if not client:
        client = FirestoreAdminAsyncClient()

    operations = []
    for model in models:
        if not model.__ttl_field__:
            continue

        # TTL policies are set per collection group, so for sub-models this is the
        # last segment of the collection template
        collection_group = model.get_collection_group_id()

        # Get current details of the field
        config_name = getattr(model, "__db_config__", "(default)")
        path = client.field_path(
            project=gcloud_project,
            database=database or configuration.get_config(config_name).database,
            collection=collection_group,
            field=model.__ttl_field__,
        )
        field_obj = await client.get_field({"name": path})

        # Variables for logging
        readable_state = str(field_obj.ttl_config.state).removeprefix("State.")
        log_str = '"%s", collection: "%s", field: "%s", state: "%s"'
        log_params = [
            model.__name__,
            collection_group,
            model.__ttl_field__,
            readable_state,
        ]

        if field_obj.ttl_config.state == Field.TtlConfig.State.STATE_UNSPECIFIED:
            logger.info("Setting up new TTL config: " + log_str, *log_params)
            field_obj.ttl_config = Field.TtlConfig({"state": Field.TtlConfig.State.CREATING})
            # Only update the TTL config, so it can't undo other changes to the field
            operation = await client.update_field(
                {"field": field_obj, "update_mask": {"paths": ["ttl_config"]}}
            )
            operations.append(operation)
        elif field_obj.ttl_config.state == Field.TtlConfig.State.CREATING:
            logger.info("TTL config is still being created: " + log_str, *log_params)
        elif field_obj.ttl_config.state == Field.TtlConfig.State.NEEDS_REPAIR:
            logger.error("TTL config needs repair: " + log_str, *log_params)
        elif field_obj.ttl_config.state == Field.TtlConfig.State.ACTIVE:
            logger.debug("TTL config is active: " + log_str, *log_params)

    return operations
