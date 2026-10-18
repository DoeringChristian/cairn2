"""Catch drift between the declared repository surface and its two adapters."""

import inspect

import pytest

from cairn.contract import RepositoryProtocol
from cairn.storage import Repository
from cairn.transport import HTTPRepository


@pytest.mark.parametrize("implementation", [Repository, HTTPRepository])
def test_repository_contract_signatures(implementation):
    for name, declaration in RepositoryProtocol.__dict__.items():
        if name.startswith("_") or not callable(declaration):
            continue
        required = inspect.signature(declaration)
        actual = inspect.signature(getattr(implementation, name))
        assert list(actual.parameters) == list(required.parameters), name
        for key, parameter in required.parameters.items():
            provided = actual.parameters[key]
            assert provided.kind == parameter.kind, (name, key)
            assert provided.default == parameter.default, (name, key)
