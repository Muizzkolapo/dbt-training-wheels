import dbtw


def test_package_importable_and_versioned():
    assert dbtw.__version__.startswith("0.1")
