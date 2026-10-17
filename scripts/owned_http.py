"""Owned HTTP protocol fixtures for installed actual SDK qualification, no provider substitute."""

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from contract_review.domain import ROLES
from contract_review.document import entire


def extracted(doc):
    return {
        "units": [
            {
                "unit_id": u["id"],
                "evidence": entire(u),
                "party": None,
                "action": entire(u),
                "deadline": None,
                "uncertainty": "Explicit synthetic human authoring; semantics require human review.",
            }
            for u in doc["units"]
        ]
    }


def assessed(doc):
    return {
        "overall": "medium",
        "assessments": [
            {
                "unit_id": u["id"],
                "evidence": entire(u),
                "severity": "medium",
                "rationale": "Explicit fictional human review example; no legal conclusion.",
                "uncertainty": "",
            }
            for u in doc["units"]
        ],
        "cross_references": [],
    }


def summarized(doc, compared):
    return {
        "narrative": "This fictional human-authored packet records literal evidence; human legal review remains pending.",
        "unit_ids": [u["id"] for u in doc["units"]],
        "notice_candidate_ids": [r["id"] for r in compared["notice_candidates"]],
        "rating_difference_ids": [r["id"] for r in compared["rating_differences"]],
    }


@contextmanager
def provider(mutate=None, before=None):
    captured = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            role = body["model"].removeprefix("owned-")
            public = json.loads(body["messages"][-1]["content"])
            captured.append(
                {
                    "role": role,
                    "body": body,
                    "public": public,
                    "headers": dict(self.headers),
                    "path": self.path,
                }
            )
            if before:
                before(role, captured)
            doc = {"units": public["source"]["body_paragraph_units"]}
            if role == "extraction":
                payload = extracted(doc)
            elif role in ("risk_anthropic", "risk_openai"):
                payload = assessed(doc)
                payload["assessments"][0][
                    "rationale"
                ] = "Synthetic independently proposed reviewer rationale."
                if role == "risk_openai":
                    payload["assessments"][0]["severity"] = "high"
            else:
                payload = summarized(doc, public["reconciliation"])
            if role in ROLES[:2]:
                response = {
                    "id": "msg-" + role,
                    "type": "message",
                    "role": "assistant",
                    "model": body["model"],
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool-" + role,
                            "name": body["tools"][0]["name"],
                            "input": payload,
                        }
                    ],
                    "stop_reason": "tool_use",
                    "stop_sequence": None,
                    "usage": {"input_tokens": 10, "output_tokens": 2},
                }
            else:
                response = {
                    "id": "chat-" + role,
                    "object": "chat.completion",
                    "created": 1,
                    "model": body["model"],
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(payload),
                                "refusal": None,
                            },
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 2,
                        "total_tokens": 12,
                    },
                }
            status = 200
            raw = None
            if mutate:
                returned = mutate(role, response, captured)
                if isinstance(returned, bytes):
                    raw = returned
                elif isinstance(returned, int):
                    status = returned
            raw = json.dumps(response).encode() if raw is None else raw
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    )
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port), captured
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
