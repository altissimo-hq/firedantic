from types import ModuleType

import firedantic


def test_all_lists_the_public_names() -> None:
    # Every public name the package imports is in __all__, and every name in
    # __all__ exists, so strict type checkers see the same API as everyone else
    public = {
        name
        for name, value in vars(firedantic).items()
        if not name.startswith("_") and not isinstance(value, ModuleType)
    }
    assert sorted(firedantic.__all__) == sorted(public)
    assert len(firedantic.__all__) == len(set(firedantic.__all__))


def test_exceptions_only_export_exceptions() -> None:
    import firedantic.exceptions

    assert all(
        issubclass(getattr(firedantic.exceptions, name), Exception)
        for name in firedantic.exceptions.__all__
    )
