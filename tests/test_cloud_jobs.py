import json
import time
import uuid

from backend import cloud_jobs


class FakeCondition:
    def __and__(self, other):
        return self

    def eq(self, value):
        return self

    def gt(self, value):
        return self

    def gte(self, value):
        return self

    def lte(self, value):
        return self

    def between(self, first, second):
        return self

    def contains(self, value):
        return self

    def not_exists(self):
        return self


def stub_condition_builders(monkeypatch):
    monkeypatch.setattr(cloud_jobs, "Attr", lambda name: FakeCondition())
    monkeypatch.setattr(cloud_jobs, "Key", lambda name: FakeCondition())


class FakeTable:
    def __init__(self):
        self.items = {}
        self.last_query = None

    def put_item(self, Item, **kwargs):
        self.items[Item["job_id"]] = Item

    def get_item(self, Key, **kwargs):
        return {"Item": self.items.get(Key["job_id"])}

    def update_item(self, Key, **kwargs):
        item = self.items[Key["job_id"]]
        values = kwargs.get("ExpressionAttributeValues", {})
        update = kwargs.get("UpdateExpression", "")
        if ":processing" in values:
            item["status"] = values[":processing"]
            item["processingStartedAt"] = values[":now"]
        elif ":status" in values:
            item.update({"status": values[":status"], "result": values[":result"], "findingCount": values[":count"], "findingDistribution": values[":distribution"], "completedAt": values[":completed"], "processingDurationMs": values[":duration"], "reviewStatus": values[":review"]})
        elif ":failed" in values:
            item.update({"status": values[":failed"], "errorCategory": values[":category"], "completedAt": values[":now"]})
        elif ":audit" in values:
            item.update({"result": values[":result"], "reviewStatus": values[":review"], "reviewedAt": values[":reviewedAt"], "auditHistory": values[":audit"]})
        return {"Attributes": item}

    def query(self, **kwargs):
        self.last_query = kwargs
        now = int(time.time())
        return {"Items": [item.copy() for item in self.items.values() if item["expires_at"] > now]}


class FakeSqs:
    def __init__(self):
        self.messages = []

    def send_message(self, **kwargs):
        self.messages.append(kwargs)
        return {"MessageId": "m-1"}


def test_submit_creates_metadata_and_sends_input_only_to_sqs(monkeypatch):
    stub_condition_builders(monkeypatch)
    table, sqs = FakeTable(), FakeSqs()
    monkeypatch.setattr(cloud_jobs, "_resources", lambda: (table, sqs, "queue-url"))
    response = cloud_jobs.submit_job("user-a", {"transcript": "MEDICATIONS: aspirin 81 mg daily."})
    item = table.items[response["jobId"]]
    message = json.loads(sqs.messages[0]["MessageBody"])
    assert response["status"] == "QUEUED"
    assert item["owner_sub"] == "user-a"
    assert "transcript" not in item and "transcription" not in item
    assert message["transcript"].startswith("MEDICATIONS")
    assert item["expires_at"] <= int(time.time()) + cloud_jobs.RETENTION_SECONDS


def test_job_reads_are_owner_scoped_and_expiry_is_enforced(monkeypatch):
    table = FakeTable()
    job_id = str(uuid.uuid4())
    table.items[job_id] = {"job_id": job_id, "owner_sub": "user-a", "expires_at": int(time.time()) + 100, "status": "QUEUED"}
    monkeypatch.setattr(cloud_jobs, "_table", lambda: table)
    assert cloud_jobs.get_job("user-a", job_id)["status"] == "QUEUED"
    assert cloud_jobs.get_job("user-b", job_id) is None
    table.items[job_id]["expires_at"] = int(time.time()) - 1
    assert cloud_jobs.get_job("user-a", job_id) is None


def test_api_requires_gateway_jwt_claims(monkeypatch):
    response = cloud_jobs.api_handler({"requestContext": {"http": {"method": "GET"}}, "rawPath": "/api/jobs"})
    assert response["statusCode"] == 401
    health = cloud_jobs.api_handler({"requestContext": {"http": {"method": "GET"}}, "rawPath": "/health"})
    assert health["statusCode"] == 200
    assert json.loads(health["body"]) == {"status": "ok"}


def test_authenticated_api_submission_returns_job_id(monkeypatch):
    stub_condition_builders(monkeypatch)
    table, sqs = FakeTable(), FakeSqs()
    monkeypatch.setattr(cloud_jobs, "_resources", lambda: (table, sqs, "queue-url"))
    event = {
        "requestContext": {"http": {"method": "POST"}, "authorizer": {"jwt": {"claims": {"sub": "user-a"}}}},
        "rawPath": "/api/jobs",
        "body": json.dumps({"transcript": "MEDICATIONS: aspirin 81 mg daily."}),
    }
    response = cloud_jobs.api_handler(event)
    assert response["statusCode"] == 202
    assert json.loads(response["body"])["status"] == "QUEUED"
    assert len(sqs.messages) == 1


def test_history_queries_owner_index_not_table_scan(monkeypatch):
    stub_condition_builders(monkeypatch)
    table = FakeTable()
    table.items["id-1"] = {"job_id": "id-1", "owner_sub": "user-a", "expires_at": int(time.time()) + 100, "submitted_at": "2026-10-16T00:00:00.000Z", "status": "COMPLETED"}
    monkeypatch.setattr(cloud_jobs, "_table", lambda: table)
    result = cloud_jobs.list_jobs("user-a", {"limit": "20", "cursor": "0"})
    assert result["total"] == 1
    assert table.last_query["IndexName"] == "ByOwnerSubmittedAt"


def test_worker_persists_result_without_persisting_input(monkeypatch):
    stub_condition_builders(monkeypatch)
    table = FakeTable()
    job_id = str(uuid.uuid4())
    table.items[job_id] = {"job_id": job_id, "owner_sub": "user-a", "expires_at": int(time.time()) + 100, "status": "QUEUED"}
    monkeypatch.setattr(cloud_jobs, "_table", lambda: table)
    from backend import web_api
    monkeypatch.setattr(web_api, "process_transcript", lambda payload: {"verification_status": "completed", "stage_b_findings": [], "completeness_review": {"human_review_required": False}})
    cloud_jobs.worker_record({"body": json.dumps({"jobId": job_id, "transcript": "synthetic input"})})
    item = table.items[job_id]
    assert item["status"] == "COMPLETED"
    assert item["result"]["verification_status"] == "completed"
    assert "transcript" not in item and "transcription" not in item
