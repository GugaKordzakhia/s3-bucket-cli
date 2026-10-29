"""Click entry point: run `poetry run s3-cli --help`."""

import json
import logging
from functools import wraps
from pathlib import Path

import click
import requests
from boto3.exceptions import S3UploadFailedError
from botocore.exceptions import BotoCoreError, ClientError

from . import s3


def handled(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            hint = ""
            if code == "AccessControlListNotSupported":
                hint = " ACLs are disabled on this bucket; use a bucket policy instead."
            elif code == "BucketNotEmpty":
                hint = " Remove objects and versions yourself before deleting the bucket."
            raise click.ClickException(f"S3 error: {code}.{hint}") from exc
        except requests.RequestException as exc:
            # Exception strings can contain signed URLs; do not echo secrets.
            raise click.ClickException("HTTP download failed; check the URL and connection.") from exc
        except (BotoCoreError, S3UploadFailedError) as exc:
            raise click.ClickException("AWS request failed; check credentials and connectivity.") from exc
        except (ValueError, OSError) as exc:
            raise click.ClickException(str(exc)) from exc
    return wrapped


def output(value):
    click.echo(json.dumps(value, indent=2, default=str, ensure_ascii=False))


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--env-file", default=".env", show_default=True, type=click.Path(dir_okay=False))
@click.option("--region", help="Override AWS_DEFAULT_REGION.")
@click.option("--endpoint-url", help="Custom S3-compatible endpoint (e.g. LocalStack).")
@click.option("--verbose", is_flag=True, help="Show application operation logs.")
@click.pass_context
def main(ctx, env_file, region, endpoint_url, verbose):
    """Manage S3 buckets, validated media uploads, ACLs, and policies."""
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("s3_bucket_cli").setLevel(logging.INFO if verbose else logging.WARNING)
    # Lazy initialization: policy generation and --help never need AWS credentials.
    ctx.obj = lambda: s3.init_client(region, endpoint_url, env_file)


@main.command("list-buckets")
@click.pass_obj
@handled
def list_command(client):
    """List bucket names and creation dates as JSON."""
    output(s3.list_buckets(client()))


@main.command("create-bucket")
@click.argument("bucket")
@click.pass_obj
@handled
def create_command(client, bucket):
    """Create a bucket in the selected region."""
    s3.create_bucket(client(), bucket)
    click.echo(f"Created: {bucket}")


@main.command("delete-bucket")
@click.argument("bucket")
@click.option("--yes", is_flag=True, help="Skip the deletion confirmation.")
@click.pass_obj
@handled
def delete_command(client, bucket, yes):
    """Delete an EMPTY bucket; objects are never removed automatically."""
    if not yes:
        click.confirm(f"Delete bucket {bucket}?", abort=True)
    s3.delete_bucket(client(), bucket)
    click.echo(f"Deleted: {bucket}")


@main.command("bucket-exists")
@click.argument("bucket")
@click.pass_obj
@handled
def exists_command(client, bucket):
    """Print true/false. Exit 0: exists; 1: missing; other failures: 1 with error."""
    exists = s3.bucket_exists(client(), bucket)
    click.echo("true" if exists else "false")
    if not exists:
        raise click.exceptions.Exit(1)


@main.command("upload-url")
@click.argument("bucket")
@click.argument("url")
@click.option("--key", help="S3 key; extension must match the detected format.")
@click.option("--max-size-mb", type=click.IntRange(min=1), default=100, show_default=True)
@click.pass_obj
@handled
def upload_command(client, bucket, url, key, max_size_mb):
    """Download HTTP(S) media and upload only BMP/JPEG/PNG/WEBP/MP4 content."""
    output(s3.download_file_and_upload_to_s3(client(), url, bucket, key,
                                           max_size_mb * 1024 * 1024))


@main.command("set-object-access")
@click.argument("bucket")
@click.argument("key")
@click.option("--acl", type=click.Choice(["private", "public-read", "bucket-owner-full-control"]),
              default="private", show_default=True)
@click.option("--yes", is_flag=True, help="Skip confirmation for public access.")
@click.pass_obj
@handled
def access_command(client, bucket, key, acl, yes):
    """Set an object ACL (requires ACL-enabled bucket)."""
    if acl == "public-read" and not yes:
        click.confirm(f"Allow everyone to read s3://{bucket}/{key}?", abort=True)
    s3.set_object_access_policy(client(), bucket, key, acl)
    click.echo(f"ACL applied: {acl}")


@main.command("generate-public-read-policy")
@click.argument("bucket")
@click.option("--partition", default="aws", show_default=True,
              type=click.Choice(["aws", "aws-cn", "aws-us-gov"]))
def generate_command(bucket, partition):
    """Print a public read policy. Does not change anything in AWS."""
    output(s3.generate_public_read_policy(bucket, partition))


@main.command("create-bucket-policy")
@click.argument("bucket")
@click.option("--file", "policy_file", required=True,
              type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--yes", is_flag=True, help="Skip policy replacement confirmation.")
@click.pass_obj
@handled
def policy_command(client, bucket, policy_file, yes):
    """Apply JSON from FILE, replacing any existing bucket policy."""
    policy = json.loads(policy_file.read_text(encoding="utf-8"))
    if not yes:
        click.confirm(f"Replace policy for {bucket} with {policy_file}? "
                      "A public policy can expose all matching objects", abort=True)
    s3.create_bucket_policy(client(), bucket, policy)
    click.echo(f"Policy applied: {bucket}")


@main.command("read-bucket-policy")
@click.argument("bucket")
@click.pass_obj
@handled
def read_command(client, bucket):
    """Print current bucket policy, or null if none exists."""
    output(s3.read_bucket_policy(client(), bucket))
