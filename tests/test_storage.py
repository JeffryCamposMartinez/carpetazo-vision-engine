import pytest
from botocore.exceptions import ClientError

from app.cli import main
from app.core.config import Settings
from app.storage.base import ObjectInfo, ObjectNotFound, StorageError
from app.storage.diagnose import diagnose, name_pattern, render_text
from app.storage.factory import get_storage
from app.storage.local import LocalStorage
from app.storage.s3 import S3Storage


def test_name_pattern():
    assert name_pattern("base1-58.webp") == "a9-9.webp"
    assert name_pattern("SV3_001_en.PNG") == "a9_9_a.png"
    assert name_pattern("README") == "a"


def test_diagnose_summarizes_structure():
    objs = [
        ObjectInfo("cards/en/base1-1.webp", 100, "e1"),
        ObjectInfo("cards/en/base1-2.webp", 300, "e2"),
        ObjectInfo("cards/es/base1-1.webp", 100, "e1"),
        ObjectInfo("logo.png", 50, None),
    ]
    report = diagnose(objs)
    d = report.to_dict()
    assert d["objects"] == 4 and d["total_bytes"] == 550
    assert d["extensions"] == {"webp": 3, "png": 1}
    assert d["prefixes"] == {"cards/": 3, "(raíz)": 1}
    assert d["objects_sharing_etag_with_another"] == 2
    assert "Objetos analizados: 4" in render_text(report, "bucket-x")


def test_diagnose_respects_max_objects():
    report = diagnose((ObjectInfo(f"a/{i}.jpg", 1) for i in range(10)), max_objects=3)
    assert report.scanned == 3 and report.truncated


def test_local_storage_roundtrip_and_traversal(tmp_path):
    st = LocalStorage(tmp_path)
    st.put("x/a.webp", b"data", "image/webp")
    assert st.get("x/a.webp", 100) == b"data"
    assert [o.key for o in st.list_objects("x/")] == ["x/a.webp"]
    with pytest.raises(ObjectNotFound):
        st.head("nope")
    with pytest.raises(StorageError):
        st.get("../outside", 10)
    with pytest.raises(StorageError):
        st.get("x/a.webp", 2)


class FakeS3Client:
    def __init__(self):
        self.calls = []

    def list_objects_v2(self, **kw):
        self.calls.append(kw)
        if "ContinuationToken" not in kw:
            return {
                "Contents": [{"Key": "a/1.webp", "Size": 10, "ETag": '"abc"'}],
                "IsTruncated": True,
                "NextContinuationToken": "t1",
            }
        return {"Contents": [{"Key": "a/2.webp", "Size": 20}], "IsTruncated": False}

    def head_object(self, Bucket, Key):
        if Key != "a/1.webp":
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {"ContentLength": 10, "ETag": '"abc"'}

    def get_object(self, Bucket, Key):
        class Body:
            def read(self, n):
                return b"x" * 10

        return {"Body": Body()}

    def put_object(self, **kw):
        self.put = kw


def test_s3_pagination_and_readonly_calls():
    client = FakeS3Client()
    st = S3Storage("b", client)
    keys = [o.key for o in st.list_objects(page_size=1)]
    assert keys == ["a/1.webp", "a/2.webp"]
    assert len(client.calls) == 2
    assert st.head("a/1.webp").etag == "abc"
    with pytest.raises(ObjectNotFound):
        st.head("missing")
    assert st.get("a/1.webp", 100) == b"x" * 10
    st.put("k", b"d", "image/webp")
    assert "ACL" not in client.put


def test_s3_requires_configuration_and_does_not_leak():
    with pytest.raises(StorageError) as exc:
        S3Storage.from_settings(Settings(s3_bucket="b"))
    assert "SECRET" in str(exc.value).upper()  # menciona nombres de variables, no valores


def test_endpoint_derived_from_account_id():
    assert Settings(r2_account_id="abc").resolved_s3_endpoint == "https://abc.r2.cloudflarestorage.com"


def test_factory_rejects_unknown_backend():
    with pytest.raises(StorageError):
        get_storage(Settings(image_storage_backend="ftp"))


def test_cli_diagnose_local(tmp_path, monkeypatch, capsys):
    (tmp_path / "set1").mkdir()
    (tmp_path / "set1" / "1.webp").write_bytes(b"x")
    monkeypatch.setenv("VISION_IMAGE_STORAGE_BACKEND", "local")
    monkeypatch.setenv("VISION_IMAGE_STORAGE_PATH", str(tmp_path))
    from app.core.config import get_settings

    get_settings.cache_clear()
    try:
        assert main(["storage-diagnose"]) == 0
    finally:
        get_settings.cache_clear()
    assert "set1/" in capsys.readouterr().out
