"""S3 operations, independent of the CLI so they can also be imported."""

import json
import logging
import os
import time
from pathlib import PurePosixPath
from tempfile import TemporaryFile
from urllib.parse import unquote, urlsplit
from uuid import uuid4

import boto3
import filetype
import requests
from botocore.config import Config
from botocore.exceptions import ClientError
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Extension must agree with the detected bytes, not with an HTTP Content-Type header.
ALLOWED_TYPES = {
    "image/bmp": (".bmp",),
    "image/jpeg": (".jpg", ".jpeg"),
    "image/png": (".png",),
    "image/webp": (".webp",),
    "video/mp4": (".mp4",),
}
DEFAULT_MAX_BYTES = 100 * 1024 * 1024


def init_client(region=None, endpoint_url=None, env_file=".env"):
    """Load dotenv without overriding the environment; preserve boto3's credential chain."""
    load_dotenv(env_file, override=False)
    return boto3.client(
        "s3",
        region_name=region or os.getenv("AWS_DEFAULT_REGION") or "us-east-1",
        endpoint_url=endpoint_url or os.getenv("S3_ENDPOINT_URL") or None,
        config=Config(retries={"max_attempts": 3, "mode": "standard"},
                      connect_timeout=10, read_timeout=60),
    )


def list_buckets(client):
    """Return all buckets, including accounts requiring paginated listing."""
    buckets = []
    for page in client.get_paginator("list_buckets").paginate():
        buckets.extend(page.get("Buckets", []))
    logger.info("Listed %d buckets", len(buckets))
    return buckets


def create_bucket(client, bucket_name, region=None):
    region = region or client.meta.region_name or "us-east-1"
    options = {"Bucket": bucket_name}
    if region != "us-east-1":
        options["CreateBucketConfiguration"] = {"LocationConstraint": region}
    result = client.create_bucket(**options)
    logger.info("Created bucket %s in %s", bucket_name, region)
    return result


def delete_bucket(client, bucket_name):
    """Delete an empty bucket only. S3 rejects nonempty buckets."""
    result = client.delete_bucket(Bucket=bucket_name)
    logger.info("Deleted bucket %s", bucket_name)
    return result


def bucket_exists(client, bucket_name):
    """False only for not-found; permission and connectivity errors remain errors."""
    try:
        client.head_bucket(Bucket=bucket_name)
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] in {"404", "NoSuchBucket", "NotFound"}:
            return False
        raise


def download_file_and_upload_to_s3(client, url, bucket_name, object_name=None,
                                   max_bytes=DEFAULT_MAX_BYTES):
    """Download bounded HTTP(S) media, detect its bytes, then upload with the real MIME.

    HTTP headers and URL suffixes are not evidence of a file's type. A misleading
    URL gets a corrected key; an explicit incompatible key is rejected.
    Temporary disk storage is removed on both success and failure.
    """
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL must be an absolute HTTP or HTTPS URL.")
    if parsed.username or parsed.password:
        raise ValueError("URLs containing credentials are not supported.")
    if max_bytes <= 0:
        raise ValueError("Maximum download size must be positive.")
    started = time.monotonic()
    with TemporaryFile(mode="w+b") as downloaded:
        with requests.get(url, stream=True, timeout=(10, 30)) as response:
            response.raise_for_status()
            if urlsplit(response.url).scheme not in {"http", "https"}:
                raise ValueError("Redirect must use HTTP or HTTPS.")
            total = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if time.monotonic() - started > 300:
                    raise ValueError("Download exceeded the 300-second limit.")
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError("File exceeds the maximum download size.")
                downloaded.write(chunk)
        if total == 0:
            raise ValueError("Downloaded file is empty.")
        downloaded.seek(0)
        detected = filetype.guess(downloaded.read(8192))
        if detected is None or detected.mime not in ALLOWED_TYPES:
            raise ValueError("Unsupported content. Allowed: BMP, JPG, JPEG, PNG, WEBP, MP4.")
        extensions = ALLOWED_TYPES[detected.mime]
        if object_name is not None:
            if not object_name.strip() or object_name.endswith("/"):
                raise ValueError("Object name must identify a file.")
            if PurePosixPath(object_name).suffix.lower() not in extensions:
                raise ValueError(f"Object extension must match {detected.mime}: {extensions}.")
            key = object_name
        else:
            # Ignore any directory components and never blindly append .jpg.
            filename = PurePosixPath(unquote(parsed.path)).name
            stem = PurePosixPath(filename).stem if filename else ""
            key = (stem or uuid4().hex) + extensions[0]
        if len(key.encode("utf-8")) > 1024:
            raise ValueError("S3 object key cannot exceed 1024 UTF-8 bytes.")
        downloaded.seek(0)
        client.upload_fileobj(downloaded, bucket_name, key,
                              ExtraArgs={"ContentType": detected.mime})
    logger.info("Uploaded %d bytes to s3://%s/%s (%s)", total, bucket_name, key, detected.mime)
    return {"bucket": bucket_name, "key": key, "content_type": detected.mime, "size": total}


def set_object_access_policy(client, bucket_name, object_name, acl="private"):
    """Set a canned object ACL; modern BucketOwnerEnforced buckets reject ACLs."""
    if acl not in {"private", "public-read", "bucket-owner-full-control"}:
        raise ValueError("Supported ACLs: private, public-read, bucket-owner-full-control.")
    result = client.put_object_acl(Bucket=bucket_name, Key=object_name, ACL=acl)
    logger.info("Set object ACL to %s for s3://%s/%s", acl, bucket_name, object_name)
    return result


def generate_public_read_policy(bucket_name, partition="aws"):
    """Generate JSON allowing anyone to read all objects (does not apply it)."""
    return {
        "Version": "2012-10-17",
        "Statement": [{"Sid": "PublicReadObjects", "Effect": "Allow", "Principal": "*",
                       "Action": "s3:GetObject",
                       "Resource": f"arn:{partition}:s3:::{bucket_name}/*"}],
    }


def create_bucket_policy(client, bucket_name, policy):
    """Apply a supplied policy. S3 replaces the existing policy, if any."""
    if isinstance(policy, str):
        policy = json.loads(policy)
    if not isinstance(policy, dict) or not isinstance(policy.get("Statement"), (list, dict)):
        raise ValueError("Policy must be a JSON object with a Statement.")
    result = client.put_bucket_policy(Bucket=bucket_name, Policy=json.dumps(policy))
    logger.info("Applied policy to bucket %s", bucket_name)
    return result


def read_bucket_policy(client, bucket_name):
    try:
        return json.loads(client.get_bucket_policy(Bucket=bucket_name)["Policy"])
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "NoSuchBucketPolicy":
            return None
        raise
