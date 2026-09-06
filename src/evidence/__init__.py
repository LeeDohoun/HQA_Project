"""Evidence package surface."""

__all__ = []


def _export(names, namespace):
    globals().update({name: namespace[name] for name in names})
    __all__.extend(names)


from .dedupe import make_document_id, make_market_record_id, make_record_id
from .source_registry import (
    DEFAULT_DOCUMENT_SOURCES,
    DEFAULT_MARKET_SOURCES,
    is_document_source,
    is_market_source,
    split_sources,
)
_export(
    [
        "make_document_id",
        "make_market_record_id",
        "make_record_id",
        "DEFAULT_DOCUMENT_SOURCES",
        "DEFAULT_MARKET_SOURCES",
        "is_document_source",
        "is_market_source",
        "split_sources",
    ],
    locals(),
)

try:
    from .index_builder import EvidenceIndexBuilder

    _export(["EvidenceIndexBuilder"], locals())
except ImportError:
    pass

try:
    from .retriever import EvidenceRetriever

    _export(["EvidenceRetriever"], locals())
except ImportError:
    pass

try:
    from .source_weighting import (
        SOURCE_WEIGHTS,
        INTENT_SOURCE_MAP,
        apply_source_weighting,
        get_source_weight,
        get_intent_sources,
        compute_freshness_multiplier,
    )

    _export(
        [
            "SOURCE_WEIGHTS",
            "INTENT_SOURCE_MAP",
            "apply_source_weighting",
            "get_source_weight",
            "get_intent_sources",
            "compute_freshness_multiplier",
        ],
        locals(),
    )
except ImportError:
    pass
