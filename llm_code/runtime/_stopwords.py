"""Stopwords for skill router tokenization — English + common code terms."""
from __future__ import annotations

STOPWORDS: frozenset[str] = frozenset({
    # English function words
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "must",
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "her",
    "us", "them", "my", "your", "his", "its", "our", "their",
    "this", "that", "these", "those", "what", "which", "who", "whom",
    "and", "or", "but", "if", "then", "else", "when", "where", "how",
    "not", "no", "nor", "so", "too", "very", "just", "also", "as",
    "of", "in", "on", "at", "to", "for", "with", "by", "from", "about",
    "into", "through", "during", "before", "after", "above", "below",
    "between", "under", "again", "further", "once", "here", "there",
    "all", "each", "every", "both", "few", "more", "most", "other",
    "some", "such", "only", "own", "same", "than", "up", "out",
    # Common in skill descriptions
    "use", "using", "used", "when", "any", "skill", "skills",
    "the", "for", "and", "with", "that", "this", "from",
    "before", "after", "work", "working", "approach",
    "ensure", "follow", "invoke",
})
