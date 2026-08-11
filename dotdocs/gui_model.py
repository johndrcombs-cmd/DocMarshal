from __future__ import annotations


class ReviewModel:
    FILTER_STATUS = {
        "Ready": "ready_for_review",
        "Needs Review": "needs_review",
        "Approved": "approved",
        "Duplicates": "duplicate",
        "Not DOT": "not_dot",
        "Failed": "failed",
    }

    def __init__(self, results: list[dict] | None = None):
        self.results = list(results or [])

    def counts(self) -> dict[str, int]:
        return {
            "total": len(self.results),
            "ready": sum(item.get("status") == "ready_for_review" for item in self.results),
            "needs_review": sum(item.get("status") == "needs_review" for item in self.results),
            "approved": sum(item.get("status") == "approved" for item in self.results),
            "failed": sum(item.get("status") == "failed" for item in self.results),
            "duplicate": sum(item.get("status") == "duplicate" for item in self.results),
            "not_dot": sum(item.get("status") == "not_dot" for item in self.results),
        }

    def filtered(self, filter_name: str) -> list[dict]:
        if filter_name == "Active":
            return [
                item
                for item in self.results
                if item.get("status") in {"ready_for_review", "needs_review", "failed"}
            ]
        status = self.FILTER_STATUS.get(filter_name)
        if status is None:
            return list(self.results)
        return [item for item in self.results if item.get("status") == status]

    def replace(self, replacement: dict) -> None:
        source_file = replacement.get("source_file")
        for index, result in enumerate(self.results):
            if result.get("source_file") == source_file:
                self.results[index] = replacement
                return
        self.results.append(replacement)
