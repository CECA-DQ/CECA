import aioboto3

from .base import StorageAdapter

# Cloudflare R2 is S3-compatible — we use boto3 with a custom endpoint.
_R2_ENDPOINT = "https://{account_id}.r2.cloudflarestorage.com"


class R2StorageAdapter(StorageAdapter):
    """Cloudflare R2 storage via the S3-compatible API.

    Required settings:
        R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET
    """

    def __init__(self, account_id: str, access_key_id: str, secret_access_key: str, bucket: str) -> None:
        self._bucket = bucket
        self._session = aioboto3.Session(
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
        )
        self._endpoint = _R2_ENDPOINT.format(account_id=account_id)

    async def upload(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        async with self._session.client("s3", endpoint_url=self._endpoint) as s3:
            await s3.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)
        return key

    async def download(self, key: str) -> bytes:
        async with self._session.client("s3", endpoint_url=self._endpoint) as s3:
            response = await s3.get_object(Bucket=self._bucket, Key=key)
            return await response["Body"].read()

    async def delete(self, key: str) -> None:
        async with self._session.client("s3", endpoint_url=self._endpoint) as s3:
            await s3.delete_object(Bucket=self._bucket, Key=key)

    async def exists(self, key: str) -> bool:
        async with self._session.client("s3", endpoint_url=self._endpoint) as s3:
            try:
                await s3.head_object(Bucket=self._bucket, Key=key)
                return True
            except s3.exceptions.ClientError:
                return False
