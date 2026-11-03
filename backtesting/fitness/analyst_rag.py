"""The analyst RAG variant of the fitness replay: the live SharedAnalysisService with retrieved
background added to the analyst's input, from both pools of `src/runner/analyst_retrieval.py` (three
theme items and two stock items) over the replay's point-in-time copy of the corpus.

The retrieval itself (BM25 over Hangul bigrams, the point-in-time and quality gates, the budget fit)
is shared with PAPER, which uses the stock pool alone (`HQA_ANALYST_RAG=stock`).
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from src.runner import analyst_retrieval
from src.runner.analysis_data import read_jsonl
from src.runner.analyst_retrieval import (  # noqa: F401  (re-exported for the replay and its tests)
    B, EXCERPT_CHARS, K1, STOCK_MAX_AGE, STOCK_QUERY_TERMS, THEME_MAX_AGE, THEME_QUERY_TERMS, UTC, Document, admit,
    bm25_scores, document, event_urls, fit_context, tokens)

VARIANT = "analyst-rag-v1"
RAG_PROMPT_VERSION = "hqa-fixed-dag-v8-backend-plan-rules+" + VARIANT
ANALYST_RAG_NOTE = (" retrieved_context lists background documents found by search: theme items about other stocks of"
                    " the same theme and older items about this stock. They are dated, may be less relevant than the"
                    " events, are not this stock's current events, and may be cited by their source_id.")


class RetrievalCorpus(analyst_retrieval.RetrievalCorpus):
    """Every admitted document revision of the replay copy, by theme."""

    @classmethod
    def from_pit(cls, pit_dir: Path) -> "RetrievalCorpus":
        members: dict[str, list[str]] = {}
        for path in sorted((Path(pit_dir) / "raw" / "theme_targets").glob("*.jsonl")):
            members[path.stem] = [str(row["stock_code"]) for row in read_jsonl(path)]
        documents, rejected = [], Counter()
        for theme in sorted(members):
            path = Path(pit_dir) / "canonical_index" / theme / "documents.jsonl"
            if not path.exists():
                continue
            for row in read_jsonl(path):
                fields, reason = admit(row)
                if fields is None:
                    rejected[reason] += 1
                    continue
                documents.append(document(row, theme, fields))
        return cls(documents, members, rejected)


def make_service_class():
    """The live SharedAnalysisService with the analyst variant: retrieved context added to the
    analyst's payload inside its leftover budget, and the variant note in the analyst prompt.
    Every other role's request is built exactly as the live service builds it."""
    from src.runner import shared_analysis
    from src.runner.analysis_data import content_hash
    from src.utils.llm_queue import LLMTaskPriority, llm_task_priority

    class RagAnalystService(shared_analysis.SharedAnalysisService):
        corpus: RetrievalCorpus

        def _specialist(self, role, payload, critical, priority=LLMTaskPriority.SCHEDULED):
            if role == "analyst":
                items = self.corpus.retrieve(payload["stock_code"], payload["stock_name"], self.clock(),
                                             exclude_urls=event_urls(payload))
                payload = fit_context(payload, items, self._input_budget("analyst"))
            return super()._specialist(role, payload, critical, priority)

        def _invoke(self, role, schema, payload, *, critical=False, priority=LLMTaskPriority.SCHEDULED):
            if role != "analyst":
                return super()._invoke(role, schema, payload, critical=critical, priority=priority)
            # The live _invoke, with the variant's analyst note and prompt version.
            prompt = shared_analysis.ROLE_INSTRUCTIONS[role] + ANALYST_RAG_NOTE
            prompt += (f" You are the {role} specialist: set role to \"{role}\" and stock_code to the supplied stock_code."
                       " Cite only IDs listed in source_ids."
                       " score is a number from 0 (strongly unfavorable) to 100 (strongly favorable) for a new long"
                       " position based only on this role's evidence; confidence is an integer from 0 to 100 for how"
                       " well the supplied evidence supports that score.")
            messages = [("system", prompt + shared_analysis.PROMPT_SUFFIX),
                        ("human", json.dumps(payload, ensure_ascii=False, allow_nan=False))]
            request_id = self.audit.append("llm_request", {
                "role": role, "model": self._model_names().get(role, shared_analysis.MODEL_VERSION),
                "prompt_version": RAG_PROMPT_VERSION, "input_hash": content_hash(payload), "holding_priority": critical,
                "instructions": messages[0][1], "payload": payload, "schema": schema.model_json_schema()}) if self.audit else None
            try:
                with llm_task_priority(LLMTaskPriority.RUNTIME if critical else priority):
                    output = self.models[role].with_structured_output(schema, method="json_schema", strict=True).invoke(messages)
                result = output if isinstance(output, schema) else schema.model_validate(output)
            except Exception as exc:
                if self.audit:
                    self.audit.append("llm_failure", {"request_id": request_id, "role": role,
                                                      "error_type": type(exc).__name__, "error": str(exc)})
                raise
            if self.audit:
                self.audit.append("llm_response", {"request_id": request_id, "role": role,
                                                  "validation": "schema_only", "output": result.model_dump(mode="json")})
            return result

    return RagAnalystService
