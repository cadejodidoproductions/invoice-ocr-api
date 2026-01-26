"""
Tests for infrastructure components (rate limiter, cache, metrics).
"""

import time
import pytest
from working_pdf_extractor import RateLimiter, ResponseCache, MetricsCollector


class TestRateLimiter:
    """Tests for the rate limiter."""

    def test_allows_requests_within_limit(self):
        """Test that requests within limit are allowed."""
        limiter = RateLimiter(max_requests=5, window_seconds=60)

        for i in range(5):
            allowed, headers = limiter.is_allowed("client1")
            assert allowed is True
            assert int(headers["X-RateLimit-Remaining"]) == 4 - i

    def test_blocks_requests_over_limit(self):
        """Test that requests over limit are blocked."""
        limiter = RateLimiter(max_requests=3, window_seconds=60)

        # Use up the limit
        for _ in range(3):
            limiter.is_allowed("client1")

        # Next request should be blocked
        allowed, headers = limiter.is_allowed("client1")
        assert allowed is False
        assert headers["X-RateLimit-Remaining"] == "0"

    def test_separate_limits_per_client(self):
        """Test that different clients have separate limits."""
        limiter = RateLimiter(max_requests=2, window_seconds=60)

        # Client 1 uses their limit
        limiter.is_allowed("client1")
        limiter.is_allowed("client1")
        allowed1, _ = limiter.is_allowed("client1")
        assert allowed1 is False

        # Client 2 should still have their limit
        allowed2, _ = limiter.is_allowed("client2")
        assert allowed2 is True

    def test_rate_limit_headers_format(self):
        """Test rate limit headers format."""
        limiter = RateLimiter(max_requests=10, window_seconds=60)
        allowed, headers = limiter.is_allowed("client1")

        assert "X-RateLimit-Limit" in headers
        assert "X-RateLimit-Remaining" in headers
        assert "X-RateLimit-Reset" in headers

        assert headers["X-RateLimit-Limit"] == "10"
        assert int(headers["X-RateLimit-Remaining"]) < 10


class TestResponseCache:
    """Tests for the response cache."""

    def test_cache_set_and_get(self):
        """Test basic cache set and get."""
        cache = ResponseCache(max_size=10, ttl_seconds=300)
        content = b"test content"
        data = {"key": "value"}

        cache.set(content, data)
        result = cache.get(content)

        assert result == data

    def test_cache_miss(self):
        """Test cache miss returns None."""
        cache = ResponseCache(max_size=10, ttl_seconds=300)
        result = cache.get(b"nonexistent")
        assert result is None

    def test_cache_returns_copy(self):
        """Test that cache returns a copy to prevent mutation."""
        cache = ResponseCache(max_size=10, ttl_seconds=300)
        content = b"test content"
        original_data = {"key": "value", "nested": {"a": 1}}

        cache.set(content, original_data)
        result = cache.get(content)

        # Modify the result
        result["key"] = "modified"

        # Original cached data should be unchanged
        result2 = cache.get(content)
        assert result2["key"] == "value"

    def test_cache_eviction_on_capacity(self):
        """Test that oldest entries are evicted when at capacity."""
        cache = ResponseCache(max_size=2, ttl_seconds=300)

        cache.set(b"content1", {"id": 1})
        time.sleep(0.01)
        cache.set(b"content2", {"id": 2})
        time.sleep(0.01)
        cache.set(b"content3", {"id": 3})  # Should evict content1

        assert cache.get(b"content1") is None
        assert cache.get(b"content2") is not None
        assert cache.get(b"content3") is not None

    def test_cache_stats(self):
        """Test cache statistics."""
        cache = ResponseCache(max_size=10, ttl_seconds=300)
        cache.set(b"content1", {"id": 1})
        cache.set(b"content2", {"id": 2})

        stats = cache.stats()

        assert stats["total_entries"] == 2
        assert stats["valid_entries"] == 2
        assert stats["max_size"] == 10
        assert stats["ttl_seconds"] == 300

    def test_cache_key_based_on_content_hash(self):
        """Test that cache key is based on content hash."""
        cache = ResponseCache(max_size=10, ttl_seconds=300)

        # Same content should hit cache
        cache.set(b"same content", {"id": 1})
        assert cache.get(b"same content") == {"id": 1}

        # Different content should miss
        assert cache.get(b"different content") is None


class TestMetricsCollector:
    """Tests for the metrics collector."""

    def test_record_successful_request(self):
        """Test recording a successful request."""
        metrics = MetricsCollector()
        metrics.record_request(success=True, processing_time=0.1)

        result = metrics.get_metrics()
        assert result["total_requests"] == 1
        assert result["successful_requests"] == 1
        assert result["failed_requests"] == 0

    def test_record_failed_request(self):
        """Test recording a failed request."""
        metrics = MetricsCollector()
        metrics.record_request(success=False, processing_time=0.1)

        result = metrics.get_metrics()
        assert result["total_requests"] == 1
        assert result["successful_requests"] == 0
        assert result["failed_requests"] == 1

    def test_cache_hit_tracking(self):
        """Test cache hit tracking."""
        metrics = MetricsCollector()
        metrics.record_request(success=True, processing_time=0.01, cache_hit=True)
        metrics.record_request(success=True, processing_time=0.1, cache_hit=False)

        result = metrics.get_metrics()
        assert result["cache_hits"] == 1
        assert result["cache_misses"] == 1
        assert result["cache_hit_rate"] == 50.0

    def test_rate_limit_tracking(self):
        """Test rate limit tracking."""
        metrics = MetricsCollector()
        metrics.record_rate_limit()
        metrics.record_rate_limit()

        result = metrics.get_metrics()
        assert result["rate_limited_requests"] == 2

    def test_auth_failure_tracking(self):
        """Test auth failure tracking."""
        metrics = MetricsCollector()
        metrics.record_auth_failure()

        result = metrics.get_metrics()
        assert result["auth_failures"] == 1

    def test_timeout_tracking(self):
        """Test timeout tracking."""
        metrics = MetricsCollector()
        metrics.record_timeout()

        result = metrics.get_metrics()
        assert result["timeout_errors"] == 1

    def test_batch_request_tracking(self):
        """Test batch request tracking."""
        metrics = MetricsCollector()
        metrics.record_batch_request()

        result = metrics.get_metrics()
        assert result["batch_requests"] == 1

    def test_average_processing_time(self):
        """Test average processing time calculation."""
        metrics = MetricsCollector()
        metrics.record_request(success=True, processing_time=0.1)
        metrics.record_request(success=True, processing_time=0.2)
        metrics.record_request(success=True, processing_time=0.3)

        result = metrics.get_metrics()
        # Average should be 0.2 seconds = 200ms
        assert abs(result["avg_processing_time_ms"] - 200.0) < 1.0

    def test_uptime_tracking(self):
        """Test uptime tracking."""
        metrics = MetricsCollector()
        time.sleep(0.1)

        result = metrics.get_metrics()
        assert result["uptime_seconds"] >= 0.1
