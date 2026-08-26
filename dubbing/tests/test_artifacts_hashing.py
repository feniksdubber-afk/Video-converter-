from dubbing.artifacts.hashing import compute_content_hash


def test_deterministic_same_input_same_hash():
    h1 = compute_content_hash(["p1", "p2"], "whisper", "1.0.0", {"lang": "uz"})
    h2 = compute_content_hash(["p1", "p2"], "whisper", "1.0.0", {"lang": "uz"})
    assert h1 == h2


def test_dict_key_order_independent():
    h1 = compute_content_hash([], "engine", "1.0", {"a": 1, "b": 2})
    h2 = compute_content_hash([], "engine", "1.0", {"b": 2, "a": 1})
    assert h1 == h2


def test_parent_hash_order_independent():
    h1 = compute_content_hash(["p1", "p2"], "engine", "1.0", {})
    h2 = compute_content_hash(["p2", "p1"], "engine", "1.0", {})
    assert h1 == h2


def test_engine_version_change_changes_hash():
    h1 = compute_content_hash([], "engine", "1.0", {})
    h2 = compute_content_hash([], "engine", "2.0", {})
    assert h1 != h2


def test_engine_name_change_changes_hash():
    h1 = compute_content_hash([], "engine_a", "1.0", {})
    h2 = compute_content_hash([], "engine_b", "1.0", {})
    assert h1 != h2


def test_params_change_changes_hash():
    h1 = compute_content_hash([], "engine", "1.0", {"x": 1})
    h2 = compute_content_hash([], "engine", "1.0", {"x": 2})
    assert h1 != h2


def test_different_parent_sets_change_hash():
    h1 = compute_content_hash(["p1"], "engine", "1.0", {})
    h2 = compute_content_hash(["p1", "p2"], "engine", "1.0", {})
    assert h1 != h2
