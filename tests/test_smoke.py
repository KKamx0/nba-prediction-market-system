import edgebot


def test_package_imports_with_version():
    assert edgebot.__version__ == "0.1.0"
