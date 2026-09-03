"""The secrets seam — phase 2 of `secrets-vault-and-identity.md`.

Two promises, each with a test that would fail if it were broken: under the default provider
behaviour is **byte-identical** to reading the environment, and the seam is an **abstraction**
— a second provider is actually consulted — without any vault being shipped.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator

import pytest

from orchestrator.core import secrets
from orchestrator.core.secrets import (
    PROVIDER_ENV,
    SecretProviderError,
    get_secret,
    register_provider,
    secret_provider,
)


@pytest.fixture(autouse=True)
def _env_provider(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Every test starts on the default provider, with any test-registered one removed after."""
    monkeypatch.delenv(PROVIDER_ENV, raising=False)
    before = dict(secrets._REGISTRY)
    yield
    secrets._REGISTRY.clear()
    secrets._REGISTRY.update(before)


def test_the_default_is_the_environment_exactly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SPINE_TEST_SECRET", "from-env")
    assert get_secret("SPINE_TEST_SECRET") == "from-env"
    monkeypatch.delenv("SPINE_TEST_SECRET")
    assert get_secret("SPINE_TEST_SECRET") is None
    assert get_secret("SPINE_TEST_SECRET", "fallback") == "fallback"


def test_an_unknown_provider_is_a_configuration_error_that_names_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Misconfiguration must not read as 'the secret is missing'. Those are different failures."""
    monkeypatch.setenv(PROVIDER_ENV, "hashicorp")
    with pytest.raises(SecretProviderError, match="hashicorp.*known: env"):
        secret_provider()


def test_a_second_provider_is_actually_consulted(monkeypatch: pytest.MonkeyPatch) -> None:
    """An interface with one implementation is indirection. This is the second one.

    A fake, registered in-test, the way a vault behind an extra would register itself. Nothing is
    shipped; what is proven is that the call sites go through the seam and not around it.
    """

    class Fake:
        def get(self, name: str) -> str | None:
            return {"OBJECT_STORE_SECRET_ACCESS_KEY": "from-fake"}.get(name)

    register_provider("fake", Fake)
    monkeypatch.setenv(PROVIDER_ENV, "fake")
    monkeypatch.setenv("OBJECT_STORE_SECRET_ACCESS_KEY", "from-env-and-must-lose")

    from orchestrator.storage.client import ObjectStoreSettings

    assert get_secret("OBJECT_STORE_SECRET_ACCESS_KEY") == "from-fake"
    assert ObjectStoreSettings.from_env().secret_access_key == "from-fake"


def test_settings_reads_credentials_through_the_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    """`Settings.api_key` under the env provider equals `ORCHESTRATOR_API_KEY` — same as before."""
    from orchestrator.registry.api.config import Settings

    monkeypatch.setenv("ORCHESTRATOR_API_KEY", "k-env")
    assert Settings().api_key == "k-env"

    class Fake:
        def get(self, name: str) -> str | None:
            return "k-fake" if name == "ORCHESTRATOR_API_KEY" else None

    register_provider("fake", Fake)
    monkeypatch.setenv(PROVIDER_ENV, "fake")
    assert Settings().api_key == "k-fake", "Settings went around the seam"


def test_init_kwargs_still_win(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every integration test builds `Settings(api_key=...)`. The seam must not reach past that."""
    from orchestrator.registry.api.config import Settings

    monkeypatch.setenv("ORCHESTRATOR_API_KEY", "k-env")
    assert Settings(api_key="k-kwarg").api_key == "k-kwarg"


def test_the_seam_imports_no_vault_client() -> None:
    """Rule 1 of the spec: the default path must not import a client.

    Otherwise 'optional' still costs every developer a dependency, and the read-only path that
    phase 1 pins to an empty environment would start needing one.

    Checked in a **fresh interpreter**, not this one: an earlier test here imports
    `storage.client`, which pulls in aioboto3 and therefore boto3, so `sys.modules` in-process
    says nothing about what `core.secrets` itself imports. The first version of this test made
    exactly that mistake and failed on boto3.
    """
    probe = (
        "import sys, orchestrator.core.secrets;"
        "print(','.join(m for m in sys.modules if m.split('.')[0] in "
        "{'hvac','boto3','botocore','aioboto3','azure','google','oci'}))"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "", f"the default path imports a client: {out.stdout.strip()}"
