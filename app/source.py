"""Shared input identity for generated papers; unchanged across UI entry points."""
import hashlib
import json


def paper_source_fingerprint(submission, assignment) -> str:
    raw = json.dumps({
        "assignment": assignment.model_dump(mode="json"),
        "code": submission.code,
        "language": submission.language,
        "source_files": [{"filename": f.filename, "line_start": f.line_start} for f in submission.source_files],
        "import_notes": submission.import_notes,
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
