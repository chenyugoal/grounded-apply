"""Shared signal for bounded local storage exhaustion across nested workflows."""


class StorageCapacityError(ValueError):
    """A validated local workflow cannot add work within its storage allowance."""
