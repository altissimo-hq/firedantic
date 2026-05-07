# Issue: Multi-Database Routing Bugs in Firedantic

During real-world testing of the new multi-database support in darwinsark, several
critical routing bugs were identified. These bugs prevent SubModel and UpdateCollection
from correctly targeting non-default databases.

## 1. Client Initialization Ignores Database Parameter

**File:** firedantic/configurations.py **Method:** get_client

**Bug:** The Client constructor is called without the database parameter, even if it is
specified in the configuration. **Impact:** All connections default to (default)
regardless of configuration. **Suggested Fix:** Pass database=cfg.database to the Client
constructor.

## 2. SubModel Routing via model_for Loses Configuration

**File:** firedantic/\_sync/model.py **Method:** BareSubCollection.model_for

**Bug:** When creating a dynamic model class for a sub-collection, the **db_config**
attribute is not copied from the base class. **Impact:** Dynamically created sub-models
revert to the (default) database. **Suggested Fix:** ic.**db_config** =
getattr(model_class, "**db_config**", "(default)")

## 3. BareSubModel.\_get_col_ref Ignores Class Config

**File:** firedantic/\_sync/model.py **Method:** BareSubModel.\_get_col_ref

**Bug:** The method calls \_get_col_ref (the helper) but doesn't pass the class's
**db_config**. **Impact:** Sub-models fail to resolve the correct client even if the
attribute is present. **Suggested Fix:** Ensure the config_name is resolved and passed
through to the client fetching logic.

## Reproduction Steps

1. Create a model with **db_config** = "backup".
2. Register a "backup" database in configuration.add(name="backup", database="backup").
3. Attempt to save a SubModel using model.model_for(parent)(...).save().
4. Observe it attempting to write to the (default) database (and failing with a 400
   error if the path doesn't exist there).
