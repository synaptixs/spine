"""PKG: Hilt/Dagger bindings as ``PROVIDES``, and the blast radius it unlocks (P4, D15).

``PROVIDES`` earned a place in a closed enum by carrying a fact nothing else does:
*which* implementation is wired behind an interface. These tests pin that claim —
including the measurement that motivated it, that ``blast_radius`` on a repository
implementation returns **nothing** without this edge, because dependency injection
means no call site ever names the implementation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.pkg import FactStore
from orchestrator.pkg.extractor import RepoCodeExtractor
from orchestrator.pkg.facts import EdgeKind, FactBatch

pytest.importorskip("tree_sitter_kotlin", reason="install the 'kotlin' extra")

DI_KT = """\
package shop.di

import dagger.Binds
import dagger.Module
import dagger.Provides
import javax.inject.Named

interface CartRepository {
    fun items(): List<String>
}

class OfflineCartRepository : CartRepository {
    override fun items(): List<String> = emptyList()
}

class FakeCartRepository : CartRepository {
    override fun items(): List<String> = emptyList()
}

class Clock

@Module
interface DataModule {
    @Binds
    fun bindsCartRepository(impl: OfflineCartRepository): CartRepository

    @Binds
    @Named("fake")
    fun bindsFake(impl: FakeCartRepository): CartRepository
}

@Module
object ClockModule {
    @Provides
    fun providesClock(): Clock = Clock()
}

class StrayBinding {
    @Binds
    fun bindsClock(impl: Clock): Clock = impl
}

class CartViewModel(private val repository: CartRepository) {
    fun load() {
        repository.items()
    }
}
"""


def _facts(tmp_path: Path, src: str = DI_KT, name: str = "Di.kt") -> FactBatch:
    (tmp_path / name).write_text(src, encoding="utf-8")
    return RepoCodeExtractor().extract(tmp_path)


def _provides(batch: FactBatch) -> set[tuple[str, str]]:
    return {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.PROVIDES}


def test_binds_points_from_the_implementation_to_the_interface(tmp_path: Path) -> None:
    """The implementation is the source because it is what a reader changes."""
    assert (
        "java:shop.di.OfflineCartRepository",
        "java:shop.di.CartRepository",
    ) in _provides(_facts(tmp_path))


def test_provides_points_from_the_factory_function(tmp_path: Path) -> None:
    """`@Provides` has no implementation type — the factory is the provider."""
    assert (
        "java:shop.di.ClockModule.providesClock",
        "java:shop.di.Clock",
    ) in _provides(_facts(tmp_path))


def test_two_qualified_providers_both_keep_their_edge(tmp_path: Path) -> None:
    """Choosing one would mean modelling Dagger's component graph (D15)."""
    provides = _provides(_facts(tmp_path))
    sources = {src for src, dst in provides if dst == "java:shop.di.CartRepository"}
    assert sources == {"java:shop.di.OfflineCartRepository", "java:shop.di.FakeCartRepository"}


def test_a_binding_outside_a_module_is_not_wiring(tmp_path: Path) -> None:
    """`StrayBinding` carries a real `@Binds`; its class carries no `@Module`."""
    assert not any(src.startswith("java:shop.di.StrayBinding") for src, _ in _provides(_facts(tmp_path)))
    assert ("java:shop.di.Clock", "java:shop.di.Clock") not in _provides(_facts(tmp_path))


def test_implements_alone_cannot_answer_the_question(tmp_path: Path) -> None:
    """The argument for the new edge kind, made concrete.

    Both implementations satisfy `IMPLEMENTS`, so it is true of the wired one and
    the fake alike. `PROVIDES` is what records that a module wired them — and here
    both are wired, which is exactly why the answer is "two providers", not one.
    """
    batch = _facts(tmp_path)
    implements = {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.IMPLEMENTS}
    assert len(implements) == 2  # IMPLEMENTS cannot distinguish them
    assert len(_provides(batch)) == 3  # PROVIDES records what each module declares


def test_blast_radius_reaches_the_injecting_code(tmp_path: Path) -> None:
    """P4's exit criterion, and the measurement that justified the edge.

    Nothing calls `OfflineCartRepository` — every call site was handed the
    interface — so its inbound edges are empty and the blast radius without
    `PROVIDES` is nothing at all. Following it outbound to the interface, and on to
    the interface's members, reaches the code that actually injects it.
    """
    batch = _facts(tmp_path)
    store = FactStore(batch)
    reached = {node.id for node, _ in store.impact_of("java:shop.di.OfflineCartRepository", max_depth=3)}
    assert "java:shop.di.CartRepository" in reached
    assert "java:shop.di.CartViewModel.load" in reached


def test_a_repo_with_no_di_gains_no_provides_edges(tmp_path: Path) -> None:
    src = "package shop.plain\n\ninterface Repo\n\nclass Impl : Repo\n"
    assert _provides(_facts(tmp_path, src, "Plain.kt")) == set()


# ---- §11 finding 5: a binding's target is a type, and a wrapper is not it ----


def _repo(tmp_path: Path, files: dict[str, str]) -> FactBatch:
    for name, src in files.items():
        f = tmp_path / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(src, encoding="utf-8")
    return RepoCodeExtractor().extract(tmp_path)


def test_a_lazy_return_type_is_peeled_to_what_it_provides(tmp_path: Path) -> None:
    """`@Provides fun provideLazyClient(): Lazy<OkHttpClient>` provides `OkHttpClient`.

    `Lazy<T>`/`Provider<T>` are indirection Dagger unwraps for you, so a provider of
    `Lazy<OkHttpClient>` makes `OkHttpClient` available exactly like a plain provider
    of `OkHttpClient` would — that is the one case `element_type`'s peel is right for.
    """
    batch = _repo(
        tmp_path,
        {
            "M.kt": """\
package app.di

import dagger.Lazy
import dagger.Module
import dagger.Provides
import okhttp3.OkHttpClient

@Module
object NetModule {
    @Provides
    fun provideLazyClient(): Lazy<OkHttpClient> = TODO()
}
"""
        },
    )
    assert ("java:app.di.NetModule.provideLazyClient", "java:okhttp3.OkHttpClient") in _provides(batch)


def test_a_set_return_type_is_not_peeled_to_its_element(tmp_path: Path) -> None:
    """#393. `@Provides fun provideClients(): Set<OkHttpClient>` provides nothing.

    `Set<OkHttpClient>` is a distinct Dagger multibinding key — not the same binding
    as a plain `OkHttpClient` provider. `element_type`'s peel used to fire here too,
    asserting a binding key that does not exist; the earlier fix for the *other*
    defect (`bare_type` minting a `Set` class in the module's own package) went one
    step too far and treated every generic return the same way.
    """
    batch = _repo(
        tmp_path,
        {
            "M.kt": """\
package app.di

import dagger.Module
import dagger.Provides
import okhttp3.OkHttpClient

@Module
object NetModule {
    @Provides
    fun provideClient(): OkHttpClient = TODO()

    @Provides
    fun provideClients(): Set<OkHttpClient> = emptySet()
}
"""
        },
    )
    provides = _provides(batch)
    assert ("java:app.di.NetModule.provideClient", "java:okhttp3.OkHttpClient") in provides
    assert not {edge for edge in provides if edge[0] == "java:app.di.NetModule.provideClients"}
    assert "java:app.di.Set" not in {n.id for n in batch.nodes}


def test_a_provided_type_the_repo_does_not_declare_is_not_put_in_its_package(tmp_path: Path) -> None:
    """The same repoint `IMPLEMENTS` has always had: a guessed target that nothing declares
    names the bare type the source wrote, never a package this front-end chose for it."""
    batch = _repo(
        tmp_path,
        {
            "M.kt": """\
package app.di

import dagger.Module
import dagger.Provides

@Module
object NetModule {
    @Provides
    fun provideClock(): Clock = TODO()
}
"""
        },
    )
    assert "java:app.di.Clock" not in {n.id for n in batch.nodes}
    assert ("java:app.di.NetModule.provideClock", "java:Clock") in _provides(batch)


def test_a_nested_wrapper_does_not_open_the_door_to_its_element(tmp_path: Path) -> None:
    """#393, the half the first fix left. `Provider<Set<Clock>>` binds `Set<Clock>`.

    The allowlist was tested on the *outermost* name and the peel that followed ran to
    the bottom, so an allowlisted wrapper let anything inside it through. This is an
    ordinary Dagger shape and nothing injecting a plain `OkHttpClient` is satisfied by
    it. One level at a time, re-asking the question at each.
    """
    batch = _repo(
        tmp_path,
        {
            "M.kt": """\
package app.di

import dagger.Module
import dagger.Provides
import javax.inject.Provider
import okhttp3.OkHttpClient

@Module
object NetModule {
    @Provides
    fun provideNested(): Provider<Set<OkHttpClient>> = TODO()
}
"""
        },
    )
    assert not _provides(batch)


def test_kotlins_own_lazy_is_not_daggers(tmp_path: Path) -> None:
    """`Lazy` with no import at all is `kotlin.Lazy`, a default import and a real type.

    Matching the allowlist on the bare name conflated it with `dagger.Lazy`, which is
    the cheapest way to hit this defect: `kotlin.Lazy` needs no import line, so nothing
    in the file says which one is meant except what is *absent*.
    """
    batch = _repo(
        tmp_path,
        {
            "M.kt": """\
package app.di

import dagger.Module
import dagger.Provides
import okhttp3.OkHttpClient

@Module
object NetModule {
    @Provides
    fun provideKotlinLazy(): Lazy<OkHttpClient> = TODO()
}
"""
        },
    )
    assert not _provides(batch)


def test_a_first_party_provider_is_not_daggers(tmp_path: Path) -> None:
    """`Provider` is an ordinary domain-model name a repository may declare itself."""
    batch = _repo(
        tmp_path,
        {
            "M.kt": """\
package app.di

import dagger.Module
import dagger.Provides
import okhttp3.OkHttpClient

class Provider<T>(val v: T)

@Module
object NetModule {
    @Provides
    fun provideOwn(): Provider<OkHttpClient> = TODO()
}
"""
        },
    )
    assert not _provides(batch)


def test_a_fully_qualified_dagger_lazy_still_binds_its_element(tmp_path: Path) -> None:
    """The recall half: `dagger.Lazy<T>` written out is still a Dagger unwrap.

    Testing the allowlist against the *written* name dropped this, because `bare_type`
    keeps the qualification — so the fix that refused `kotlin.Lazy` would have refused
    the real one too if it compared names rather than resolved ids.
    """
    batch = _repo(
        tmp_path,
        {
            "M.kt": """\
package app.di

import dagger.Module
import dagger.Provides
import okhttp3.OkHttpClient

@Module
object NetModule {
    @Provides
    fun provideLazy(): dagger.Lazy<OkHttpClient> = TODO()
}
"""
        },
    )
    assert ("java:app.di.NetModule.provideLazy", "java:okhttp3.OkHttpClient") in _provides(batch)
