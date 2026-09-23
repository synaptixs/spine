"""Sequelize models in JavaScript → Entity / Field / REFERENCES (javascript-support-roadmap P4)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("tree_sitter_typescript", reason="install the 'typescript' extra")

from orchestrator.pkg.data_layer_link import link_data_layer  # noqa: E402
from orchestrator.pkg.extractor import RepoCodeExtractor  # noqa: E402
from orchestrator.pkg.facts import EdgeKind, FactBatch, NodeKind  # noqa: E402
from orchestrator.pkg.js_extractor import JavaScriptExtractor  # noqa: E402


def _repo(tmp_path: Path, files: dict[str, str]) -> FactBatch:
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return RepoCodeExtractor(extractors=[JavaScriptExtractor()]).extract(tmp_path)


def _entities(batch: FactBatch) -> set[str]:
    return {n.id for n in batch.nodes if n.kind is NodeKind.ENTITY}


def _fields(batch: FactBatch) -> set[str]:
    return {n.id for n in batch.nodes if n.kind is NodeKind.FIELD and n.id.startswith("ts:entity:")}


def _refs(batch: FactBatch) -> set[tuple[str, str]]:
    return {(e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.REFERENCES}


_USER = (
    "const { DataTypes } = require('sequelize');\n"
    "module.exports = (sequelize) => {\n"
    "  sequelize.define('user', {\n"
    "    id: { primaryKey: true, type: DataTypes.INTEGER },\n"
    "    username: { type: DataTypes.STRING, validate: { len: [3] } },\n"
    "  });\n"
    "};\n"
)


def test_a_model_defined_inside_a_function_is_an_entity(tmp_path: Path) -> None:
    """The official example's shape: the connection is a parameter, the define is in its body."""
    batch = _repo(tmp_path, {"models/user.js": _USER})
    assert _entities(batch) == {"ts:entity:user"}
    assert ("ts:models/user", "ts:entity:user") in {
        (e.src, e.dst) for e in batch.edges if e.kind is EdgeKind.CONTAINS
    }


def test_columns_are_the_attribute_keys_and_not_their_options(tmp_path: Path) -> None:
    """`type` and `validate` are properties *of* a column, not columns."""
    batch = _repo(tmp_path, {"models/user.js": _USER})
    assert _fields(batch) == {"ts:entity:user.id", "ts:entity:user.username"}


def test_define_without_the_sequelize_import_is_not_a_model(tmp_path: Path) -> None:
    """No ORM marker, no Entity: `define` is an ordinary method name."""
    batch = _repo(tmp_path, {"registry.js": "function setup(r) { r.define('user', { id: 1 }); }\n"})
    assert not _entities(batch)


def test_a_class_extending_sequelizes_model_is_an_entity(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "post.js": (
                "const { Model, DataTypes } = require('sequelize');\n"
                "class Post extends Model {}\n"
                "Post.init({ title: DataTypes.STRING, body: DataTypes.TEXT }, { sequelize });\n"
                "module.exports = { Post };\n"
            )
        },
    )
    assert _entities(batch) == {"ts:entity:Post"}
    assert _fields(batch) == {"ts:entity:Post.title", "ts:entity:Post.body"}


def test_a_base_merely_named_model_is_not_sequelizes(tmp_path: Path) -> None:
    """Objection.js names its base `Model` too. The marker is the binding, not the name."""
    batch = _repo(
        tmp_path,
        {"person.js": "const { Model } = require('objection');\nclass Person extends Model {}\n"},
    )
    assert not _entities(batch)


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        ("instrument.belongsTo(orchestra);", ("ts:entity:instrument", "ts:entity:orchestra")),
        ("orchestra.hasMany(instrument);", ("ts:entity:instrument", "ts:entity:orchestra")),
        ("orchestra.hasOne(instrument);", ("ts:entity:instrument", "ts:entity:orchestra")),
    ],
)
def test_references_follow_the_foreign_key(tmp_path: Path, call: str, expected: tuple[str, str]) -> None:
    """`belongsTo` keys the receiver; `hasMany`/`hasOne` key the argument."""
    batch = _repo(tmp_path, _two_models(call))
    assert _refs(batch) == {expected}


def test_the_redundant_pair_states_one_foreign_key(tmp_path: Path) -> None:
    batch = _repo(tmp_path, _two_models("orchestra.hasMany(instrument);\ninstrument.belongsTo(orchestra);"))
    assert _refs(batch) == {("ts:entity:instrument", "ts:entity:orchestra")}


def test_a_models_registry_access_names_the_model(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path, _two_models("", setup="sequelize.models.instrument.belongsTo(sequelize.models.orchestra);")
    )
    assert _refs(batch) == {("ts:entity:instrument", "ts:entity:orchestra")}


def test_belongs_to_many_draws_no_edge(tmp_path: Path) -> None:
    """Its keys live on a join table the source need not declare."""
    batch = _repo(tmp_path, _two_models("orchestra.belongsToMany(instrument, { through: 'x' });"))
    assert not _refs(batch)


def test_an_association_naming_an_undefined_model_is_dropped(tmp_path: Path) -> None:
    """Both ends are names. `finalize` keeps the edge only when both are entities.

    `ghost` and `phantom` are destructured from the registry, so they *resolve* — and the edge is
    drawn and then dropped by `finalize`. An earlier version left them undeclared, so resolution
    returned None first and the test passed with the existence check switched off.
    """
    setup = (
        "const { instrument, orchestra, ghost, phantom } = sequelize.models;\n"
        "ghost.belongsTo(orchestra);\ninstrument.belongsTo(phantom);"
    )
    batch = _repo(tmp_path, _two_models("", setup=setup))
    assert not _refs(batch)


def test_the_orm_entity_collapses_onto_a_sql_table(tmp_path: Path) -> None:
    """`data_layer_link` needs nothing JavaScript-specific — it matches by name. The schema is
    authoritative, so the ORM entity folds onto `sql:users`."""
    pytest.importorskip("sqlglot", reason="install the 'sql' extra")
    for rel, text in {
        "models/user.js": _USER,
        "schema.sql": "CREATE TABLE users (id INT PRIMARY KEY, username TEXT);\n",
    }.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    batch = link_data_layer(RepoCodeExtractor().extract(tmp_path))
    entities = _entities(batch)
    assert any(e.startswith("sql:") for e in entities)
    assert "ts:entity:user" not in entities


def _two_models(associations: str, *, setup: str = "") -> dict[str, str]:
    define = (
        "const {{ DataTypes }} = require('sequelize');\n"
        "module.exports = (sequelize) => {{ sequelize.define('{name}', {{ id: DataTypes.INTEGER }}); }};\n"
    )
    body = setup or f"const {{ instrument, orchestra }} = sequelize.models;\n{associations}"
    return {
        "models/orchestra.js": define.format(name="orchestra"),
        "models/instrument.js": define.format(name="instrument"),
        "setup.js": (
            f"function applyExtraSetup(sequelize) {{\n{body}\n}}\nmodule.exports = {{ applyExtraSetup }};\n"
        ),
    }


# ── review pass 2 ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "call",
    [
        pytest.param("customElements.define('my-el', { connectedCallback() {} });", id="custom-elements"),
        pytest.param("ajv.define('userSchema', { type: 'object', properties: {} });", id="ajv"),
        pytest.param("factory.define('user', { name: 'x' });", id="factory"),
    ],
)
def test_define_with_an_untyped_attribute_map_is_not_a_model(tmp_path: Path, call: str) -> None:
    """A file importing sequelize — a test's `{ Op }` — may call other libraries' `define`."""
    batch = _repo(tmp_path, {"t.js": f"const {{ Op }} = require('sequelize');\n{call}\n"})
    assert not _entities(batch)


def test_an_import_names_the_model_its_module_defines_not_its_local(tmp_path: Path) -> None:
    """`const Author = require('./models/user')` is the `user` model, whatever else is `Author`."""
    define = (
        "const {{ DataTypes }} = require('sequelize');\n"
        "module.exports = (s) => {{ s.define('{m}', {{ id: DataTypes.INTEGER }}); }};\n"
    )
    batch = _repo(
        tmp_path,
        {
            "models/user.js": define.format(m="user"),
            "models/author.js": define.format(m="Author"),
            "models/post.js": define.format(m="post"),
            "setup.js": (
                "const Author = require('./models/user');\n"
                "const Post = require('./models/post');\n"
                "Post.belongsTo(Author);\n"
            ),
        },
    )
    assert _refs(batch) == {("ts:entity:post", "ts:entity:user")}


def test_an_abstract_base_is_not_an_entity_and_its_subclass_is(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "m.js": (
                "const { Model, DataTypes } = require('sequelize');\n"
                "class BaseModel extends Model {}\nclass Product extends BaseModel {}\n"
                "Product.init({ sku: DataTypes.STRING }, {});\n"
            )
        },
    )
    assert _entities(batch) == {"ts:entity:Product"}


def test_model_name_sets_the_id_and_table_name_the_name_the_schema_matches(tmp_path: Path) -> None:
    pytest.importorskip("sqlglot", reason="install the 'sql' extra")
    for rel, text in {
        "u.js": (
            "const { Model, DataTypes } = require('sequelize');\nclass User extends Model {}\n"
            "User.init({ id: DataTypes.INTEGER }, { modelName: 'account', tableName: 'legacy_accounts' });\n"
        ),
        "schema.sql": (
            "CREATE TABLE legacy_accounts (id INT PRIMARY KEY);\nCREATE TABLE users (id INT PRIMARY KEY);\n"
        ),
    }.items():
        (tmp_path / rel).write_text(text)
    batch = RepoCodeExtractor().extract(tmp_path)
    account = next(n for n in batch.nodes if n.id == "ts:entity:account")
    assert account.name == "legacy_accounts"
    assert "ts:entity:account" not in _entities(link_data_layer(batch))  # folded onto sql:legacy_accounts


def test_the_getting_started_binding_names_the_model_and_self_associations_are_skipped(
    tmp_path: Path,
) -> None:
    batch = _repo(
        tmp_path,
        {
            "m.js": (
                "const { Sequelize, DataTypes } = require('sequelize');\n"
                "const sequelize = new Sequelize('sqlite::memory:');\n"
                "const User = sequelize.define('User', { name: DataTypes.STRING });\n"
                "const Task = sequelize.define('Task', { title: DataTypes.STRING });\n"
                "User.hasMany(Task);\nUser.hasMany(User);\n"
            )
        },
    )
    assert _refs(batch) == {("ts:entity:Task", "ts:entity:User")}


def test_a_non_literal_name_or_a_spread_column_is_not_guessed(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "m.js": (
                "const { DataTypes } = require('sequelize');\n"
                "module.exports = (s, name, base) => {\n"
                "  s.define(name, { id: DataTypes.INTEGER });\n"
                "  s.define('item', { ...base, sku: DataTypes.STRING });\n};\n"
            )
        },
    )
    assert _entities(batch) == {"ts:entity:item"}
    assert _fields(batch) == {"ts:entity:item.sku"}


def test_a_comment_inside_the_arguments_is_not_an_argument(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "m.js": (
                "const { DataTypes } = require('sequelize');\n"
                "module.exports = (s) => { s.define(/* c */ 'gadget', { id: DataTypes.INTEGER }); };\n"
            )
        },
    )
    assert _entities(batch) == {"ts:entity:gadget"}


# ── review pass 3 ──────────────────────────────────────────────────────────────────────


def test_parameterized_types_mark_a_model(tmp_path: Path) -> None:
    """`STRING(120)`, `DECIMAL(10, 2)`, `ENUM(…)` — pass 2's marker missed every one."""
    batch = _repo(
        tmp_path,
        {
            "m.js": (
                "const { DataTypes } = require('sequelize');\n"
                "module.exports = (s) => { s.define('article', {\n"
                "  title: DataTypes.STRING(120), total: DataTypes.DECIMAL(10, 2),\n"
                "  status: { type: DataTypes.ENUM('new', 'paid') },\n});\n};\n"
            )
        },
    )
    assert _entities(batch) == {"ts:entity:article"}
    assert len(_fields(batch)) == 3


# ── review pass 4 ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "column",
    [
        pytest.param("DataTypes.INTEGER.UNSIGNED", id="modifier"),
        pytest.param("DataTypes.INTEGER(11).UNSIGNED", id="parameterized-modifier"),
        pytest.param("{ type: DataTypes.STRING.BINARY, allowNull: false }", id="modifier-in-options"),
        pytest.param("DataTypes.BIGINT.UNSIGNED.ZEROFILL", id="two-modifiers"),
        pytest.param("DataTypes.NUMERIC", id="numeric"),
        pytest.param("DataTypes.NUMERIC(10, 2)", id="numeric-parameterized"),
    ],
)
def test_a_type_anywhere_in_the_chain_marks_a_model(tmp_path: Path, column: str) -> None:
    """Each the *only* column, so each must mark the model on its own — the join-table case."""
    batch = _repo(
        tmp_path,
        {
            "m.js": (
                "const { DataTypes } = require('sequelize');\n"
                f"sequelize.define('gig', {{ a: {column} }});\n"
            )
        },
    )
    assert _entities(batch) == {"ts:entity:gig"}


def test_a_modifier_alone_is_not_a_type(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "m.js": (
                "const { DataTypes } = require('sequelize');\n"
                "sequelize.define('gig', { a: DataTypes.UNSIGNED });\n"
            )
        },
    )
    assert not _entities(batch)


@pytest.mark.parametrize(
    "call",
    [
        pytest.param("scopes.define('active', { deletedAt: Op.is });", id="operator"),
        pytest.param("registry.define('audit', { createdAt: Sequelize.NOW });", id="default-value"),
    ],
)
def test_a_sequelize_value_that_is_not_a_type_marks_nothing(tmp_path: Path, call: str) -> None:
    batch = _repo(
        tmp_path,
        {
            "m.js": (
                f"const {{ Op }} = require('sequelize');\nconst Sequelize = require('sequelize');\n{call}\n"
            )
        },
    )
    assert not _entities(batch)


def test_an_esm_aliased_import_names_the_export_it_was_imported_as(tmp_path: Path) -> None:
    """`import { admin as user }` is the `admin` model, whatever else is called `user`."""
    define = "import {{ DataTypes }} from 'sequelize';\nexport default (s) => {{ {body} }};\n"
    batch = _repo(
        tmp_path,
        {
            "models/two.js": define.format(
                body=(
                    "s.define('user', { id: DataTypes.INTEGER }); "
                    "s.define('admin', { id: DataTypes.INTEGER });"
                )
            ),
            "models/post.js": define.format(body="s.define('post', { id: DataTypes.INTEGER });"),
            "setup.js": (
                "import { admin as user } from './models/two';\n"
                "import Post from './models/post';\n"
                "Post.belongsTo(user);\n"
            ),
        },
    )
    assert _refs(batch) == {("ts:entity:post", "ts:entity:admin")}


def test_a_named_import_matches_by_name_never_by_being_the_only_model(tmp_path: Path) -> None:
    """`user.js` defines `user` and re-exports `Post`: `{ Post }` from it is not `user`."""
    d = (
        "const {{ DataTypes }} = require('sequelize');\n"
        "module.exports = (s) => {{ s.define('{m}', {{ id: DataTypes.INTEGER }}); }};\n"
    )
    batch = _repo(
        tmp_path,
        {
            "models/user.js": d.format(m="user") + "module.exports.Post = require('./post');\n",
            "models/post.js": d.format(m="post"),
            "models/comment.js": d.format(m="comment"),
            "setup.js": (
                "const { Post } = require('./models/user');\nconst Comment = require('./models/comment');\n"
                "Comment.belongsTo(Post);\n"
            ),
        },
    )
    assert ("ts:entity:comment", "ts:entity:user") not in _refs(batch)


def test_a_whole_module_import_of_a_module_with_several_models_is_ambiguous(tmp_path: Path) -> None:
    """`settle`'s disambiguation: two models, a whole-module import — no guess."""
    d = "const {{ DataTypes }} = require('sequelize');\nmodule.exports = (s) => {{ {body} }};\n"
    batch = _repo(
        tmp_path,
        {
            "models/two.js": d.format(
                body=(
                    "s.define('user', { id: DataTypes.INTEGER }); "
                    "s.define('admin', { id: DataTypes.INTEGER });"
                )
            ),
            "models/post.js": d.format(body="s.define('post', { id: DataTypes.INTEGER });"),
            "setup.js": (
                "const Two = require('./models/two');\n"
                "const Post = require('./models/post');\n"
                "Post.belongsTo(Two);\n"
            ),
        },
    )
    assert not _refs(batch)


def test_a_named_import_matches_its_model_case_aside(tmp_path: Path) -> None:
    """`const { Post }` names the `post` model: associations are written with the class's case.

    Only where the module's exports cannot be read — here an `Object.assign` onto them. A module
    whose export map *is* read decides for itself (see the next two tests), and a default-only
    `module.exports = (s) => {…}` exports no `Post` at all: this fixture was that, once, and
    asserted an edge whose import is `undefined` at run time.
    """
    d = "const {{ DataTypes }} = require('sequelize');\nmodule.exports = (s) => {{ {body} }};\n"
    batch = _repo(
        tmp_path,
        {
            "models/pair.js": (
                "const { DataTypes } = require('sequelize');\nconst s = require('./db');\nconst m = {};\n"
                "m.post = s.define('post', { id: DataTypes.INTEGER });\n"
                "m.tag = s.define('tag', { id: DataTypes.INTEGER });\n"
                "Object.assign(module.exports, { Post: m.post, Tag: m.tag });\n"
            ),
            "models/comment.js": d.format(body="s.define('comment', { id: DataTypes.INTEGER });"),
            "setup.js": (
                "const { Post } = require('./models/pair');\nconst Comment = require('./models/comment');\n"
                "Comment.belongsTo(Post);\n"
            ),
        },
    )
    assert _refs(batch) == {("ts:entity:comment", "ts:entity:post")}


def test_a_named_import_is_the_binding_it_exports_not_the_model_name(tmp_path: Path) -> None:
    """`{ Booking }` exports a *variable*; the model it holds is `gig`, in no case `Booking`."""
    d = "const {{ DataTypes }} = require('sequelize');\nmodule.exports = (s) => {{ {body} }};\n"
    model = (
        "const { DataTypes } = require('sequelize');\nconst s = require('./db');\n"
        "const Booking = s.define('gig', { id: DataTypes.INTEGER });\n"
    )
    batch = _repo(
        tmp_path,
        {
            "models/shorthand.js": model + "module.exports = { Booking };\n",
            "models/renamed.js": model.replace("gig", "show").replace("Booking", "Show")
            + "module.exports = { Event: Show };\n",
            "models/direct.js": (
                "const { DataTypes } = require('sequelize');\nconst s = require('./db');\n"
                "exports.Post = s.define('article', { id: DataTypes.INTEGER });\n"
            ),
            "models/comment.js": d.format(body="s.define('comment', { id: DataTypes.INTEGER });"),
            "setup.js": (
                "const { Booking } = require('./models/shorthand');\n"
                "const { Event } = require('./models/renamed');\n"
                "const Post = require('./models/direct').Post;\n"
                "const Comment = require('./models/comment');\n"
                "Comment.belongsTo(Booking);\nComment.belongsTo(Event);\nComment.belongsTo(Post);\n"
            ),
        },
    )
    assert _refs(batch) == {
        ("ts:entity:comment", "ts:entity:gig"),
        ("ts:entity:comment", "ts:entity:show"),
        ("ts:entity:comment", "ts:entity:article"),
    }


def test_a_read_export_map_refuses_a_name_it_does_not_list(tmp_path: Path) -> None:
    """The map is read in full, so a name it lacks is not exported — no case-aside guess at `post`."""
    batch = _repo(
        tmp_path,
        {
            "models/user.js": (
                "const { DataTypes } = require('sequelize');\nconst s = require('./db');\n"
                "const post = s.define('post', { id: DataTypes.INTEGER });\n"
                "const { Post } = require('./post');\nmodule.exports = { post, Post };\n"
            ),
            "models/comment.js": (
                "const { DataTypes } = require('sequelize');\nconst s = require('./db');\n"
                "const Comment = s.define('comment', { id: DataTypes.INTEGER });\n"
                "const { Post } = require('./user');\nComment.belongsTo(Post);\n"
            ),
        },
    )
    # `user.js` exports `Post` as a re-export of `./post`, not as its own `post` model.
    assert _refs(batch) == set()


def test_a_read_export_map_refuses_a_model_it_does_not_export(tmp_path: Path) -> None:
    """`post` is a model in the file but not an export of it; `{ post }` is `undefined` at run time."""
    batch = _repo(
        tmp_path,
        {
            "models/pair.js": (
                "const { DataTypes } = require('sequelize');\nconst s = require('./db');\n"
                "const post = s.define('post', { id: DataTypes.INTEGER });\n"
                "const Tag = s.define('tag', { id: DataTypes.INTEGER });\nmodule.exports = { Tag };\n"
            ),
            "models/comment.js": (
                "const { DataTypes } = require('sequelize');\nconst s = require('./db');\n"
                "const Comment = s.define('comment', { id: DataTypes.INTEGER });\n"
                "const { post, Tag } = require('./pair');\n"
                "Comment.belongsTo(post);\nComment.belongsTo(Tag);\n"
            ),
        },
    )
    assert _refs(batch) == {("ts:entity:comment", "ts:entity:tag")}


def test_an_exact_model_name_wins_over_a_case_aside_one(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "models/pair.js": (
                "const { DataTypes } = require('sequelize');\nconst s = require('./db');\n"
                "s.define('post', { id: DataTypes.INTEGER });\ns.define('Post', { id: DataTypes.INTEGER });\n"
                "Object.assign(module.exports, s.models);\n"
            ),
            "setup.js": (
                "const { DataTypes } = require('sequelize');\nconst s = require('./db');\n"
                "const Comment = s.define('comment', { id: DataTypes.INTEGER });\n"
                "const { Post } = require('./models/pair');\nComment.belongsTo(Post);\n"
            ),
        },
    )
    assert _refs(batch) == {("ts:entity:comment", "ts:entity:Post")}


def test_the_model_base_is_the_binding_so_an_alias_is_read(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "m.js": (
                "import { Model as SeqModel, DataTypes } from 'sequelize';\n"
                "class Widget extends SeqModel {}\nWidget.init({ id: DataTypes.INTEGER }, {});\n"
            )
        },
    )
    assert _entities(batch) == {"ts:entity:Widget"}


def test_define_reads_the_table_name_from_its_options(tmp_path: Path) -> None:
    batch = _repo(
        tmp_path,
        {
            "m.js": (
                "const { DataTypes } = require('sequelize');\n"
                "module.exports = (s) => {\n"
                "  s.define('post', { id: DataTypes.INTEGER }, { tableName: 'blog_entries' });\n"
                "};\n"
            )
        },
    )
    post = next(n for n in batch.nodes if n.id == "ts:entity:post")
    assert post.name == "blog_entries"


# ---- js-review-followup P4: `settle` on the three tiers


_HEAD = "const { DataTypes } = require('sequelize');\nconst s = require('./db');\n"
_ORCH = _HEAD + "module.exports = { Orchestra: s.define('orchestra', { id: DataTypes.INTEGER }) };\n"


def _setup(imported: str, spec: str = "./models/m") -> str:
    return (
        f"const {imported} = require('{spec}');\nconst {{ Orchestra }} = require('./models/o');\n"
        f"function wire() {{ {imported.strip('{} ').split(':')[-1].strip()}.belongsTo(Orchestra); }}\n"
    )


def _joined(tmp_path: Path, model_file: str, imported: str) -> set[tuple[str, str]]:
    batch = _repo(tmp_path, {"models/m.js": model_file, "models/o.js": _ORCH, "setup.js": _setup(imported)})
    return {pair for pair in _refs(batch) if pair[1] == "ts:entity:orchestra"}


_TO = {("ts:entity:post", "ts:entity:orchestra")}


@pytest.mark.parametrize(
    "module",
    [
        pytest.param(
            "const db = {};\ndb.Post = s.define('post', { t: DataTypes.STRING });\nmodule.exports = db;\n",
            id="index-object",
        ),
        pytest.param(
            "var api = exports = module.exports = {};\n"
            "api.Post = s.define('post', { t: DataTypes.STRING });\n",
            id="express-alias",
        ),
        pytest.param(
            "module.exports = { Post: s.define('post', { t: DataTypes.STRING }) };\n", id="in-the-literal"
        ),
        pytest.param(
            "module.exports.Post = s.define('post', { t: DataTypes.STRING });\n", id="module-exports-member"
        ),
        pytest.param(
            "exports.Article = exports.Post = s.define('post', { t: DataTypes.STRING });\n", id="chained"
        ),
        pytest.param(
            "export const Post = s.define('post', { t: DataTypes.STRING });\n", id="esm-export-const"
        ),
        pytest.param(
            "const Draft = s.define('post', { t: DataTypes.STRING });\nexport { Draft as Post };\n",
            id="esm-export-as",
        ),
    ],
)
def test_a_model_written_onto_the_exports_is_that_export(tmp_path: Path, module: str) -> None:
    """S1, ORM N4: a define under its export key however the key is spelled — an alias, the
    exported literal, every link of a chain, an ESM export."""
    assert _joined(tmp_path, _HEAD + module, "{ Post }") == _TO


def test_a_chained_export_is_every_name_in_the_chain(tmp_path: Path) -> None:
    module = _HEAD + "exports.Article = exports.Post = s.define('post', { t: DataTypes.STRING });\n"
    assert _joined(tmp_path, module, "{ Article }") == _TO


@pytest.mark.parametrize(
    "module",
    [
        pytest.param(
            "exports.Post = s.define('post', { t: DataTypes.STRING });\n"
            "module.exports = Object.freeze({ other: 1 });\n",
            id="frozen-replacement",
        ),
        pytest.param(
            "module.exports = make();\nexports.Post = s.define('post', { t: DataTypes.STRING });\n",
            id="made-replacement",
        ),
        pytest.param(
            "const Post = s.define('post', { t: DataTypes.STRING });\nmodule.exports = Post;\n",
            id="a-default-not-a-member",
        ),
        pytest.param(
            "function build() { const Post = s.define('post', { t: DataTypes.STRING }); return Post; }\n"
            "const Post = null;\n"
            "module.exports = { Post, build };\n",
            id="function-local-binding",
        ),
        pytest.param(
            "exports.api.Post = s.define('entry', { t: DataTypes.STRING });\nmixin(exports);\n",
            id="member-of-a-member",
        ),
    ],
)
def test_a_named_import_the_module_does_not_export_as_a_model_resolves_to_nothing(
    tmp_path: Path, module: str
) -> None:
    """S2, S4, S5, ORM N6: each of these once invented `post → orchestra`."""
    assert _joined(tmp_path, _HEAD + module, "{ Post }") == set()


@pytest.mark.parametrize(
    "module",
    [
        pytest.param(
            "const Post = s.define('post', { t: DataTypes.STRING });\n"
            "if (typeof module !== 'undefined') { module.exports = { Post }; }\n",
            id="umd-branch",
        ),
        pytest.param(
            "const Post = s.define('post', { t: DataTypes.STRING });\n"
            "if (a) { module.exports = {}; } else { module.exports = { Post }; }\n",
            id="either-branch",
        ),
    ],
)
def test_an_ambiguous_surface_keeps_every_model_any_branch_exports(tmp_path: Path, module: str) -> None:
    """S3: an ambiguous `module.exports` is the union of its names, never an empty map enforced."""
    assert _joined(tmp_path, _HEAD + module, "{ Post }") == _TO


def test_a_whole_module_import_reaches_the_default_even_beside_other_models(tmp_path: Path) -> None:
    module = (
        _HEAD
        + "const { Model } = require('sequelize');\nconst other = s.define('tag', { t: DataTypes.STRING });\n"
        "class Post extends Model {}\n"
        "Post.init({ t: DataTypes.STRING }, { sequelize: s, modelName: 'post' });\n"
        "module.exports = Post;\n"
    )
    assert _joined(tmp_path, module, "Player") == _TO


def test_a_renamed_export_of_a_model_class_resolves_through_the_class(tmp_path: Path) -> None:
    """ORM N6: `classes` must be part of what `scan` returns, or `{ Player: Musician }` is lost."""
    module = (
        _HEAD + "const { Model } = require('sequelize');\nclass Musician extends Model {}\n"
        "Musician.init({ t: DataTypes.STRING }, { sequelize: s, modelName: 'post' });\n"
        "module.exports = { Player: Musician };\n"
    )
    assert _joined(tmp_path, module, "{ Player }") == _TO


def test_a_case_aside_match_must_be_unique(tmp_path: Path) -> None:
    """ORM N6: two models equal case aside are ambiguous; neither is picked."""
    module = (
        _HEAD + "s.define('post', { t: DataTypes.STRING });\ns.define('Post', { t: DataTypes.STRING });\n"
        "mixin(module.exports);\n"
    )
    assert _joined(tmp_path, module, "{ POST }") == set()


def test_a_bare_type_key_in_an_option_object_is_not_a_column_type(tmp_path: Path) -> None:
    """ORM N6: `{ type: 'string' }` is an option whose value is no Sequelize type."""
    batch = _repo(tmp_path, {"m.js": _HEAD + "s.define('x', { kind: { type: 'string' } });\n"})
    assert _entities(batch) == set()


def test_a_self_association_revealed_only_by_resolution_is_dropped(tmp_path: Path) -> None:
    """ORM N6: two locals that settle to the same model are one table keyed to itself."""
    model = _HEAD + "module.exports = { Post: s.define('post', { t: DataTypes.STRING }) };\n"
    setup = (
        "const { Post } = require('./models/m');\nconst Same = require('./models/m');\n"
        "function wire() { Post.belongsTo(Same); }\n"
    )
    batch = _repo(tmp_path, {"models/m.js": model, "setup.js": setup})
    assert _refs(batch) == set()


@pytest.mark.parametrize(
    "module",
    [
        pytest.param("export const Post = s.define('entry', { t: DataTypes.STRING });\n", id="export-const"),
        pytest.param(
            "const Draft = s.define('entry', { t: DataTypes.STRING });\nexport { Draft as Post };\n",
            id="export-as",
        ),
    ],
)
def test_an_esm_export_names_the_model_its_declaration_holds(tmp_path: Path, module: str) -> None:
    """D8: an ESM export is a readable declaration — the binding, not the model's name, decides."""
    assert _joined(tmp_path, _HEAD + module, "{ Post }") == {("ts:entity:entry", "ts:entity:orchestra")}


def test_an_opaque_module_does_not_export_an_unexported_binding(tmp_path: Path) -> None:
    """S2: the deleted step (2). `const Post` at the top level of a module whose exports are
    hidden is not therefore an export called `Post`."""
    module = _HEAD + "const Post = s.define('entry', { t: DataTypes.STRING });\nmodule.exports = make();\n"
    assert _joined(tmp_path, module, "{ Post }") == set()


def test_a_function_between_two_var_declarations_reads_the_last(tmp_path: Path) -> None:
    """Review round 3, B1: `wire()` runs after the module has loaded, whatever its position —
    `Post` is the second `var` by then, not the one written above the function."""
    module = (
        _HEAD + "var Post = s.define('draft', { t: DataTypes.STRING });\n"
        "function wire(x) { Post.belongsTo(x.models.orchestra); }\n"
        "var Post = s.define('post', { t: DataTypes.STRING });\nmodule.exports = { wire };\n"
    )
    batch = _repo(tmp_path, {"models/m.js": module, "models/o.js": _ORCH})
    assert {pair for pair in _refs(batch) if pair[1] == "ts:entity:orchestra"} == _TO


def test_a_file_that_declares_its_own_module_exports_no_model(tmp_path: Path) -> None:
    """Review round 3, S2: `var module = …` is the file's own; the real module exports `{}`."""
    module = (
        _HEAD
        + "var module = { exports: {} };\n"
        + "module.exports = { Post: s.define('post', { t: DataTypes.STRING }) };\n"
    )
    assert _joined(tmp_path, module, "{ Post }") == set()


@pytest.mark.parametrize(
    "between",
    [
        pytest.param("(function () { Post.belongsTo(s.models.orchestra); })();\n", id="iife"),
        pytest.param("(() => { Post.belongsTo(s.models.orchestra); })();\n", id="arrow-iife"),
        pytest.param(
            "class W {\n  static {\n    Post.belongsTo(s.models.orchestra);\n  }\n}\n", id="static-block"
        ),
    ],
)
def test_code_that_runs_where_it_stands_reads_the_declaration_above(tmp_path: Path, between: str) -> None:
    """Review round 4, S2: a function invoked on the spot and a `static {}` block run between the
    two declarations, while `Post` is still the first."""
    module = (
        _HEAD
        + "var Post = s.define('post', { t: DataTypes.STRING });\n"
        + between
        + "var Post = s.define('final', { t: DataTypes.STRING });\n"
    )
    batch = _repo(tmp_path, {"models/m.js": module, "models/o.js": _ORCH})
    assert {pair for pair in _refs(batch) if pair[1] == "ts:entity:orchestra"} == _TO
