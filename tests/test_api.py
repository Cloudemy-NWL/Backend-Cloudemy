from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.routers import internal, submissions


class FakeUpdateResult:
    def __init__(self, matched_count: int):
        self.matched_count = matched_count


class FakeCollection:
    def __init__(self) -> None:
        self._storage: Dict[str, Dict[str, Any]] = {}

    async def insert_one(self, document: Dict[str, Any]):
        self._storage[document["_id"]] = copy.deepcopy(document)

    async def find_one(self, query: Dict[str, Any]):
        submission_id = query.get("_id")
        if submission_id is None:
            return None

        doc = self._storage.get(submission_id)
        if not doc:
            return None

        # Mimic Motor returning a plain dict (avoid mutating original)
        return copy.deepcopy(doc)

    async def update_one(self, query: Dict[str, Any], update: Dict[str, Any]):
        submission_id = query.get("_id")
        if submission_id is None:
            return FakeUpdateResult(matched_count=0)

        doc = self._storage.get(submission_id)
        if not doc:
            return FakeUpdateResult(matched_count=0)

        finalized_constraint = query.get("finalized")
        if (
            isinstance(finalized_constraint, dict)
            and finalized_constraint.get("$ne") is True
            and doc.get("finalized") is True
        ):
            return FakeUpdateResult(matched_count=0)

        set_payload = update.get("$set", {})
        doc.update(copy.deepcopy(set_payload))
        self._storage[submission_id] = doc
        return FakeUpdateResult(matched_count=1)


@pytest.fixture
def app_with_fakes(monkeypatch) -> Tuple[FastAPI, FakeCollection, List[Dict[str, Any]]]:
    fake_collection = FakeCollection()
    enqueue_calls: List[Dict[str, Any]] = []

    async def _fake_enqueue(message: Dict[str, Any]) -> None:
        enqueue_calls.append(message)

    monkeypatch.setattr(submissions, "COLL", lambda: fake_collection)
    monkeypatch.setattr(internal, "COLL", lambda: fake_collection)
    monkeypatch.setattr(submissions, "_enqueue_to_queue", _fake_enqueue)

    test_app = FastAPI()
    test_app.include_router(submissions.router)
    test_app.include_router(internal.router)
    return test_app, fake_collection, enqueue_calls


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_submission_lifecycle(app_with_fakes):
    app, _, enqueue_calls = app_with_fakes

    async with AsyncClient(app=app, base_url="http://test") as client:
        # Create submission
        create_payload = {
            "assignment_id": "assign-1",
            "language": "python",
            "code": "print('hello world')",
        }
        create_resp = await client.post("/submissions", json=create_payload)
        assert create_resp.status_code == 201

        queued = create_resp.json()
        submission_id = queued["submission_id"]
        assert queued["status"] == "QUEUED"
        assert enqueue_calls and enqueue_calls[0]["submission_id"] == submission_id

        # Fetch submission
        get_resp = await client.get(f"/submissions/{submission_id}")
        assert get_resp.status_code == 200
        submission_data = get_resp.json()
        assert submission_data["status"] == "QUEUED"
        assert submission_data["metrics"] == {"timeMs": 0, "memoryMB": 0}

        # Post runner result
        result_payload = {
            "status": "COMPLETED",
            "score": 95.5,
            "fail_tags": ["style"],
            "feedback": [{"case": "case-1", "message": "Looks good"}],
            "metrics": {"timeMs": 1234, "memoryMB": 64},
        }
        result_resp = await client.post(
            f"/internal/submissions/{submission_id}/result",
            headers={"X-Result-Token": "secret"},
            json=result_payload,
        )
        assert result_resp.status_code == 200
        assert result_resp.json()["status"] == "COMPLETED"

        # Updated submission reflects new data
        updated_resp = await client.get(f"/submissions/{submission_id}")
        assert updated_resp.status_code == 200
        updated = updated_resp.json()
        assert updated["status"] == "COMPLETED"
        assert updated["score"] == 95.5
        assert updated["metrics"] == {"timeMs": 1234, "memoryMB": 64}
        assert updated["feedback"][0]["case"] == "case-1"

        # Finalize submission
        finalize_resp = await client.post(
            f"/submissions/{submission_id}/finalize", json={"note": "Approved"}
        )
        assert finalize_resp.status_code == 200
        finalize_data = finalize_resp.json()
        assert finalize_data["status"] == "FINALIZED"
        assert finalize_data["finalized"] is True

        # Finalize again to ensure idempotency
        repeat_finalize = await client.post(
            f"/submissions/{submission_id}/finalize", json={"note": "Approved"}
        )
        assert repeat_finalize.status_code == 200
        assert repeat_finalize.json()["status"] == "FINALIZED"

