# SQL has moved to oasis.index.keyword.KeywordIndex.
# Re-export types and markers so existing imports keep working.
from oasis.index.keyword import MATCH_END, MATCH_START, Result as SearchResult  # noqa: F401
