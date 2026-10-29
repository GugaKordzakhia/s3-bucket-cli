import io
import json
from types import SimpleNamespace
from unittest.mock import Mock

import boto3
import pytest
import requests
from botocore.exceptions import ClientError
from botocore.stub import Stubber
from click.testing import CliRunner
from PIL import Image

from s3_bucket_cli import s3
from s3_bucket_cli.cli import main


@pytest.fixture
def client():
    return boto3.client("s3", region_name="us-east-1", aws_access_key_id="testing",
                        aws_secret_access_key="testing")


def error(code):
    return ClientError({"Error": {"Code": code, "Message": "test"}}, "HeadBucket")


@pytest.mark.parametrize("region,expected", [
    ("us-east-1", {"Bucket": "test-bucket"}),
    ("eu-west-1", {"Bucket": "test-bucket", "CreateBucketConfiguration": {
        "LocationConstraint": "eu-west-1"}}),
])
def test_create_region(client, region, expected):
    with Stubber(client) as stub:
        stub.add_response("create_bucket", {}, expected)
        s3.create_bucket(client, "test-bucket", region)
        stub.assert_no_pending_responses()


def test_bucket_operations(client):
    with Stubber(client) as stub:
        stub.add_response("list_buckets", {"Buckets": [{"Name": "test-bucket"}]}, {})
        stub.add_response("head_bucket", {}, {"Bucket": "test-bucket"})
        stub.add_client_error("head_bucket", "404", http_status_code=404,
                              expected_params={"Bucket": "missing"})
        stub.add_client_error("head_bucket", "403", http_status_code=403,
                              expected_params={"Bucket": "forbidden"})
        stub.add_response("delete_bucket", {}, {"Bucket": "test-bucket"})
        assert s3.list_buckets(client)[0]["Name"] == "test-bucket"
        assert s3.bucket_exists(client, "test-bucket")
        assert not s3.bucket_exists(client, "missing")
        with pytest.raises(ClientError):
            s3.bucket_exists(client, "forbidden")
        s3.delete_bucket(client, "test-bucket")
        stub.assert_no_pending_responses()


def test_policy_and_acl(client):
    policy = s3.generate_public_read_policy("test-bucket")
    assert policy["Statement"][0]["Resource"] == "arn:aws:s3:::test-bucket/*"
    with Stubber(client) as stub:
        stub.add_response("put_bucket_policy", {}, {"Bucket": "test-bucket",
                                                    "Policy": json.dumps(policy)})
        stub.add_response("get_bucket_policy", {"Policy": json.dumps(policy)},
                          {"Bucket": "test-bucket"})
        stub.add_client_error("get_bucket_policy", "NoSuchBucketPolicy",
                              expected_params={"Bucket": "test-bucket"})
        stub.add_response("put_object_acl", {}, {"Bucket": "test-bucket", "Key": "x.jpg",
                                                "ACL": "private"})
        s3.create_bucket_policy(client, "test-bucket", policy)
        assert s3.read_bucket_policy(client, "test-bucket") == policy
        assert s3.read_bucket_policy(client, "test-bucket") is None
        s3.set_object_access_policy(client, "test-bucket", "x.jpg")
        stub.assert_no_pending_responses()


def response(monkeypatch, payload, status_error=None):
    mocked = Mock()
    mocked.url = "https://example.test/media.jpg"
    mocked.__enter__ = Mock(return_value=mocked)
    mocked.__exit__ = Mock(return_value=False)
    mocked.raise_for_status.side_effect = status_error
    mocked.iter_content.return_value = iter([payload[:10], b"", payload[10:]])
    monkeypatch.setattr(s3.requests, "get", Mock(return_value=mocked))
    return mocked


def media(format):
    data = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(data, format=format)
    return data.getvalue()


@pytest.mark.parametrize("format,extension,mime", [
    ("BMP", ".bmp", "image/bmp"), ("JPEG", ".jpg", "image/jpeg"),
    ("JPEG", ".jpeg", "image/jpeg"), ("PNG", ".png", "image/png"),
    ("WEBP", ".webp", "image/webp"),
])
def test_allowed_images(monkeypatch, format, extension, mime):
    payload = media(format)
    response(monkeypatch, payload)
    client = Mock()
    received = []
    client.upload_fileobj.side_effect = lambda file, *a, **kw: received.append(file.read())
    result = s3.download_file_and_upload_to_s3(client, "https://example.test/fake.jpg",
                                              "test-bucket", "folder/file" + extension)
    assert result["content_type"] == mime
    assert received == [payload]
    assert client.upload_fileobj.call_args.kwargs["ExtraArgs"] == {"ContentType": mime}


def test_mp4_detection(monkeypatch):
    # ISO BMFF MP4 signature fixture; signature detection is not full video decoding.
    payload = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
    response(monkeypatch, payload)
    result = s3.download_file_and_upload_to_s3(Mock(), "https://example.test/video", "bucket")
    assert result["content_type"] == "video/mp4"
    assert result["key"] == "video.mp4"


@pytest.mark.parametrize("payload", [b"", b"<html>not a jpeg</html>", b"%PDF-1.7 test",
                                       b"GIF89a" + b"\x00" * 30])
def test_disallowed_content_never_uploads(monkeypatch, payload):
    response(monkeypatch, payload)
    client = Mock()
    with pytest.raises(ValueError):
        s3.download_file_and_upload_to_s3(client, "https://example.test/fake.jpg", "bucket")
    client.upload_fileobj.assert_not_called()


def test_mismatched_explicit_extension(monkeypatch):
    response(monkeypatch, media("PNG"))
    client = Mock()
    with pytest.raises(ValueError, match="extension"):
        s3.download_file_and_upload_to_s3(client, "https://example.test/a.jpg", "b", "a.jpg")
    client.upload_fileobj.assert_not_called()


def test_misleading_url_is_corrected(monkeypatch):
    response(monkeypatch, media("PNG"))
    result = s3.download_file_and_upload_to_s3(Mock(), "https://example.test/a.jpg?token=x", "b")
    assert result["key"] == "a.png"


def test_size_limit(monkeypatch):
    response(monkeypatch, media("PNG"))
    client = Mock()
    with pytest.raises(ValueError, match="maximum"):
        s3.download_file_and_upload_to_s3(client, "https://example.test/a.png", "b", max_bytes=10)
    client.upload_fileobj.assert_not_called()


def test_http_failure(monkeypatch):
    response(monkeypatch, b"", requests.HTTPError("404"))
    client = Mock()
    with pytest.raises(requests.HTTPError):
        s3.download_file_and_upload_to_s3(client, "https://example.test/a.png", "b")
    client.upload_fileobj.assert_not_called()


@pytest.mark.parametrize("url", ["file:///tmp/x.jpg", "ftp://example.test/a", "https:///a",
                                  "https://user:password@example.test/a"])
def test_invalid_url(url):
    with pytest.raises(ValueError):
        s3.download_file_and_upload_to_s3(Mock(), url, "b")


def test_cli_offline_and_confirmation(monkeypatch):
    monkeypatch.setattr(s3, "init_client", Mock(side_effect=AssertionError("No AWS needed")))
    runner = CliRunner()
    assert runner.invoke(main, ["--help"]).exit_code == 0
    result = runner.invoke(main, ["generate-public-read-policy", "test-bucket"])
    assert result.exit_code == 0
    assert json.loads(result.output)["Version"] == "2012-10-17"
    assert runner.invoke(main, ["delete-bucket", "test-bucket"], input="n\n").exit_code == 1


def test_dotenv_preserves_environment(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("AWS_DEFAULT_REGION=eu-west-1\n")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    mocked = Mock(return_value=SimpleNamespace())
    monkeypatch.setattr(s3.boto3, "client", mocked)
    s3.init_client(env_file=env)
    assert mocked.call_args.kwargs["region_name"] == "us-east-1"
