from database.state import AssetStore


def test_asset_store_returns_only_new_assets(tmp_path) -> None:
    assets = [{"hostname": "api.example.com", "addresses": ["192.0.2.1"]}]
    with AssetStore(tmp_path / "assets.db") as store:
        assert store.record_scan("example.com", assets) == assets
        assert store.record_scan("example.com", assets) == []
