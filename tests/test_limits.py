import base64

from my_bank_api.limits import MAX_REQUEST_SIZE
from my_bank_api.schemas import MAX_ATTACHMENT_SIZE

API = "/api/v1"


def test_largest_attachment_still_fits(client, make):
    acc = make.account()
    tx = make.tx(acc["id"], "-14.80")
    content = base64.b64encode(b"x" * MAX_ATTACHMENT_SIZE).decode()
    r = client.put(f"{API}/transactions/{tx['id']}/attachment", json={"kind": "file", "filename": "f.pdf", "content_base64": content})
    assert r.status_code == 200


def test_announced_size_over_limit_is_refused_before_reading(client):
    body = b"x" * (MAX_REQUEST_SIZE + 1)
    r = client.post(f"{API}/import/ms", content=body, headers={"Content-Type": "application/json", "Accept-Language": "en"})
    assert r.status_code == 413 and r.json()["detail"] == "Request too large (over 14 MiB)"


def test_chunked_body_over_limit_is_refused(client):
    def chunks():
        for _ in range(MAX_REQUEST_SIZE // (1024 * 1024) + 2):
            yield b"x" * (1024 * 1024)

    r = client.post(f"{API}/import/ms", content=chunks(), headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and "Mio" in r.json()["detail"]
