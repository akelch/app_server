import math
import time
import urllib.parse
from datetime import datetime
from http import HTTPStatus

from ..exceptions import NotFound


def _make_object_resource(base_url, bucket_name, object_name, content_type, content_length):
    time_id = math.floor(time.time())
    now = str(datetime.now())

    return {
        "kind": "storage#object",
        "id": "{}/{}/{}".format(bucket_name, object_name, time_id),
        "selfLink": "/storage/v1/b/{}/o/{}".format(bucket_name, object_name),
        "name": object_name,
        "bucket": bucket_name,
        "generation": str(time_id),
        "metageneration": "1",
        "contentType": content_type,
        "timeCreated": now,
        "updated": now,
        "storageClass": "STANDARD",
        "timeStorageClassUpdated": now,
        "size": content_length,
        "md5Hash": "NOT_IMPLEMENTED",
        "mediaLink": "{}/download/storage/v1/b/{}/o/{}?generation={}&alt=media".format(
            base_url,
            bucket_name,
            object_name,
            time_id,
        ),
        "crc32c": "lj+ong==",
        "etag": "CO6Q4+qNnOcCEAE="
    }


def _multipart_upload(request, response, storage):
    obj = _make_object_resource(
        request.base_url,
        request.params["bucket_name"],
        request.data["meta"]["name"],
        request.data["content-type"],
        str(len(request.data["content"])),
    )

    storage.create_file(
        request.params["bucket_name"],
        request.data["meta"]["name"],
        request.data["content"],
        obj,
    )

    response.json(obj)


def _create_resumable_upload(request, response, storage):
    content_type = request.get_header('x-upload-content-type', 'application/octet-stream')
    content_length = request.get_header('x-upload-content-length', None)

    if isinstance(request.data,bytes):
        return upload_partial(request,response,storage)

    obj = _make_object_resource(
        request.base_url,
        request.params["bucket_name"],
        request.data["name"],
        content_type,
        content_length,
    )

    id = storage.create_resumable_upload(
        request.params["bucket_name"],
        request.data["name"],
        obj,
    )

    encoded_id = urllib.parse.urlencode({
        'upload_id': id,
    })
    response["Location"] = request.full_url + "&{}".format(encoded_id)


def insert(request, response, storage, *args, **kwargs):
    uploadType = request.query.get("uploadType")

    if not uploadType or len(uploadType) == 0:
        response.status = HTTPStatus.BAD_REQUEST
        return

    uploadType = uploadType[0]

    if uploadType == "resumable":
        return _create_resumable_upload(request, response, storage)

    if uploadType == "multipart":
        return _multipart_upload(request, response, storage)


def upload_partial(request, response, storage, *args, **kwargs):
    upload_id = request.query.get("upload_id")[0]
    obj = storage.create_file_for_resumable_upload(upload_id, request.data)
    return response.json(obj)


def get(request, response, storage, *args, **kwargs):
    if request.query.get("alt") == ["media"]:
        return download(request, response, storage, *args, **kwargs)
    try:
        obj = storage.get_file_obj(request.params["bucket_name"], request.params["object_id"])
        response.json(obj)
    except NotFound:
        response.status = HTTPStatus.NOT_FOUND


def ls(request, response, storage, *args, **kwargs):
    bucket_name = request.params["bucket_name"]
    prefix = request.query.get("prefix")[0] if request.query.get("prefix") else None
    delimiter = request.query.get('delimiter')[0] if request.query.get("delimiter") else None
    try:
        files = storage.get_file_list(bucket_name, prefix, delimiter)
    except NotFound:
        response.status = HTTPStatus.NOT_FOUND
    else:
        response.json({
            "kind": "storage#object",
            "items": files
        })


def copy(request, response, storage, *args, **kwargs):
    try:
        obj = storage.get_file_obj(request.params["bucket_name"], request.params["object_id"])
    except NotFound:
        response.status = HTTPStatus.NOT_FOUND
        return

    dest_obj = _make_object_resource(
        request.base_url,
        request.params["dest_bucket_name"],
        request.params["dest_object_id"],
        obj["contentType"],
        obj["size"],
    )

    file = storage.get_file(request.params["bucket_name"], request.params["object_id"])
    storage.create_file(request.params["dest_bucket_name"], request.params["dest_object_id"], file, dest_obj)

    response.json(dest_obj)


def download(request, response, storage, *args, **kwargs):
    try:
        file = storage.get_file(request.params["bucket_name"], request.params["object_id"])
        obj = storage.get_file_obj(request.params["bucket_name"], request.params["object_id"])
        response.write_file(file, content_type=obj.get("contentType"))
    except NotFound:
        response.status = HTTPStatus.NOT_FOUND


def delete(request, response, storage, *args, **kwargs):
    try:
        storage.delete_file(request.params["bucket_name"], request.params["object_id"])
    except NotFound:
        response.status = HTTPStatus.NOT_FOUND
