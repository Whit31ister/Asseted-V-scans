from database.state import AssetStore


def test_asset_store_returns_only_new_assets(tmp_path) -> None:
    assets = [{"hostname": "api.example.com", "addresses": ["192.0.2.1"]}]
    with AssetStore(tmp_path / "assets.db") as store:
        assert store.record_scan("example.com", assets) == assets
        assert store.record_scan("example.com", assets) == []


def test_asset_store_records_scan_runs_and_history(tmp_path) -> None:
    path = tmp_path / "assets.db"
    with AssetStore(path) as store:
        run_id = store.start_scan_run("example.com")
        assets = [
            {"hostname": "api.example.com", "addresses": ["192.0.2.1"]},
            {"hostname": "www.example.com", "addresses": ["192.0.2.2"]},
        ]
        new_assets = store.record_scan("example.com", assets)
        store.finish_scan_run(
            run_id,
            "completed",
            asset_count=2,
            new_count=2,
            probe_count=3,
            error="Discord notification failed with HTTP 500",
        )

    with AssetStore(path) as store:
        runs = store.list_scan_runs(target="example.com")
        assert len(runs) == 1
        assert runs[0]["id"] == run_id
        assert runs[0]["status"] == "completed"
        assert runs[0]["asset_count"] == 2
        assert runs[0]["probe_count"] == 3
        assert runs[0]["finished_at"] is not None
        assert runs[0]["error"] == "Discord notification failed with HTTP 500"

        assert store.list_targets() == ["example.com"]
        assert store.known_hostnames("example.com") == {
            "api.example.com",
            "www.example.com",
        }
        assert len(new_assets) == 2

        assets = store.list_assets(target="example.com")
        assert [asset["hostname"] for asset in assets] == [
            "api.example.com",
            "www.example.com",
        ]
        assert assets[0]["addresses"] == ["192.0.2.1"]
        assert assets[0]["first_seen"] is not None
        assert assets[0]["last_seen"] is not None

        filtered = store.list_assets(search="www")
        assert [asset["hostname"] for asset in filtered] == [
            "www.example.com"
        ]
