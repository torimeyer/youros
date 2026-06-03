"""Tests for api/services/embeddings.py (P2)."""
import math
import pytest
from services.embeddings import chunk_text, cosine


class TestChunkText:
    def test_empty(self):
        assert chunk_text("") == []

    def test_short_text_single_chunk(self):
        result = chunk_text("hello world", chunk_size=800)
        assert result == ["hello world"]

    def test_chunk_boundaries(self):
        words = ["word"] * 1000
        text = " ".join(words)
        chunks = chunk_text(text, chunk_size=100, overlap=20)
        assert len(chunks) > 1
        for c in chunks:
            assert len(c.split()) <= 100

    def test_overlap(self):
        words = list(range(200))
        text = " ".join(str(w) for w in words)
        chunks = chunk_text(text, chunk_size=50, overlap=25)
        assert len(chunks) >= 2
        first_end_words = set(chunks[0].split()[-25:])
        second_start_words = set(chunks[1].split()[:25])
        assert first_end_words & second_start_words

    def test_at_least_one_chunk_nonempty(self):
        result = chunk_text("x", chunk_size=800)
        assert result == ["x"]

    def test_exact_chunk_size(self):
        words = ["w"] * 100
        text = " ".join(words)
        chunks = chunk_text(text, chunk_size=100, overlap=0)
        assert len(chunks) == 1

    def test_batch_limit_respected(self):
        words = ["a"] * 300
        text = " ".join(words)
        chunks = chunk_text(text, chunk_size=100, overlap=0)
        assert len(chunks) == 3


class TestCosine:
    def test_identical_vectors_is_one(self):
        v = [1.0, 2.0, 3.0]
        result = cosine(v, v)
        assert abs(result - 1.0) < 1e-9

    def test_orthogonal_is_zero(self):
        a = [1.0, 0.0, 0.0]
        b = [0.0, 1.0, 0.0]
        assert abs(cosine(a, b)) < 1e-9

    def test_opposite_is_minus_one(self):
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert abs(cosine(a, b) - (-1.0)) < 1e-9

    def test_zero_vector_returns_zero(self):
        a = [0.0, 0.0]
        b = [1.0, 2.0]
        assert cosine(a, b) == 0.0
        assert cosine(b, a) == 0.0

    def test_partial_similarity(self):
        a = [1.0, 1.0]
        b = [1.0, 0.0]
        result = cosine(a, b)
        assert 0.0 < result < 1.0
