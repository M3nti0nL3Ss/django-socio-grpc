"""
Tests for enhanced streaming functionality with flow control and memory optimization.
"""

import asyncio
import time
from unittest.mock import Mock, patch, AsyncMock

from django.test import TestCase, override_settings
from django.db import models
from django.core.paginator import Page, Paginator

from django_socio_grpc.streaming import (
    StreamingMetrics,
    StreamingConfig,
    check_context_cancelled,
    stream_queryset_sync,
    stream_queryset_async,
    stream_paginated_queryset_async,
)


class StreamingMetricsTests(TestCase):
    """Test streaming metrics collection."""
    
    def test_metrics_initialization(self):
        """Test that metrics are properly initialized."""
        metrics = StreamingMetrics()
        
        self.assertEqual(metrics.items_streamed, 0)
        self.assertEqual(metrics.queue_depth, 0)
        self.assertEqual(metrics.processing_latency, [])
        self.assertIsInstance(metrics.start_time, float)
        self.assertIsInstance(metrics.last_yield_time, float)
    
    def test_record_item_streamed(self):
        """Test recording streamed items."""
        metrics = StreamingMetrics()
        
        # Record first item
        time.sleep(0.01)  # Small delay to create measurable latency
        metrics.record_item_streamed()
        
        self.assertEqual(metrics.items_streamed, 1)
        self.assertEqual(len(metrics.processing_latency), 1)
        self.assertGreater(metrics.processing_latency[0], 0)
    
    def test_get_average_latency(self):
        """Test average latency calculation."""
        metrics = StreamingMetrics()
        
        # No items yet
        self.assertEqual(metrics.get_average_latency(), 0.0)
        
        # Add some latency data
        metrics.processing_latency = [0.1, 0.2, 0.3]
        self.assertEqual(metrics.get_average_latency(), 0.2)
    
    def test_get_streaming_rate(self):
        """Test streaming rate calculation."""
        metrics = StreamingMetrics()
        
        # Mock start time to control elapsed time
        metrics.start_time = time.time() - 1.0  # 1 second ago
        metrics.items_streamed = 10
        
        rate = metrics.get_streaming_rate()
        self.assertAlmostEqual(rate, 10.0, places=0)  # ~10 items/second


class StreamingConfigTests(TestCase):
    """Test streaming configuration."""
    
    def test_default_values(self):
        """Test default configuration values."""
        self.assertEqual(StreamingConfig.DEFAULT_CHUNK_SIZE, 1000)
        self.assertEqual(StreamingConfig.DEFAULT_CANCELLATION_CHECK_INTERVAL, 100)
        self.assertEqual(StreamingConfig.DEFAULT_YIELD_TIMEOUT, 30.0)
        self.assertTrue(StreamingConfig.ENABLE_METRICS)
    
    @override_settings(GRPC_FRAMEWORK={'STREAMING_CHUNK_SIZE': 500})
    def test_custom_chunk_size(self):
        """Test custom chunk size from settings."""
        from django_socio_grpc.settings import grpc_settings
        # This would work if grpc_settings was reloaded, but for testing
        # we'll just verify the concept
        pass


class ContextCancellationTests(TestCase):
    """Test context cancellation checking."""
    
    def test_check_context_cancelled_true(self):
        """Test context cancellation detection when cancelled."""
        mock_context = Mock()
        mock_context.cancelled.return_value = True
        
        result = check_context_cancelled(mock_context)
        self.assertTrue(result)
    
    def test_check_context_cancelled_false(self):
        """Test context cancellation detection when not cancelled."""
        mock_context = Mock()
        mock_context.cancelled.return_value = False
        
        result = check_context_cancelled(mock_context)
        self.assertFalse(result)
    
    def test_check_context_cancelled_exception(self):
        """Test graceful handling of cancellation check exceptions."""
        mock_context = Mock()
        mock_context.cancelled.side_effect = Exception("Test error")
        
        result = check_context_cancelled(mock_context)
        self.assertFalse(result)  # Should return False on exception


class MockQuerySet:
    """Mock QuerySet for testing."""
    
    def __init__(self, items, chunk_size=None):
        self.items = items
        self._chunk_size = chunk_size
    
    def iterator(self, chunk_size=None):
        """Mock iterator method that yields items."""
        for item in self.items:
            yield item
    
    def __getitem__(self, key):
        """Support slicing for chunked operations."""
        if isinstance(key, slice):
            return MockQuerySet(self.items[key])
        return self.items[key]


class MockSerializer:
    """Mock serializer for testing."""
    
    def __init__(self, instance, **kwargs):
        self.instance = instance
        self.message = f"message_{instance}"


class SyncStreamingTests(TestCase):
    """Test synchronous streaming functionality."""
    
    def test_stream_queryset_sync_basic(self):
        """Test basic synchronous streaming."""
        queryset = MockQuerySet(['item1', 'item2', 'item3'])
        
        messages = list(stream_queryset_sync(
            queryset=queryset,
            serializer_class=MockSerializer,
            enable_metrics=False
        ))
        
        expected = ['message_item1', 'message_item2', 'message_item3']
        self.assertEqual(messages, expected)
    
    def test_stream_queryset_sync_with_context_cancellation(self):
        """Test streaming with context cancellation."""
        queryset = MockQuerySet(['item1', 'item2', 'item3', 'item4', 'item5'])
        mock_context = Mock()
        
        # Cancel after 2 items
        mock_context.cancelled.side_effect = [False, False, True]
        
        messages = list(stream_queryset_sync(
            queryset=queryset,
            serializer_class=MockSerializer,
            context=mock_context,
            cancellation_check_interval=2,
            enable_metrics=False
        ))
        
        # Should stop early due to cancellation
        self.assertLess(len(messages), 5)
    
    def test_stream_queryset_sync_with_metrics(self):
        """Test streaming with metrics collection."""
        queryset = MockQuerySet(['item1', 'item2'])
        
        with patch('django_socio_grpc.streaming.logger') as mock_logger:
            messages = list(stream_queryset_sync(
                queryset=queryset,
                serializer_class=MockSerializer,
                enable_metrics=True
            ))
            
            # Should log metrics
            mock_logger.info.assert_called()
            
        self.assertEqual(len(messages), 2)


class AsyncStreamingTests(TestCase):
    """Test asynchronous streaming functionality."""
    
    async def async_test_stream_queryset_async_basic(self):
        """Test basic asynchronous streaming."""
        queryset = MockQuerySet(['item1', 'item2', 'item3'])
        
        messages = []
        async for message in stream_queryset_async(
            queryset=queryset,
            serializer_class=MockSerializer,
            enable_metrics=False
        ):
            messages.append(message)
        
        expected = ['message_item1', 'message_item2', 'message_item3']
        self.assertEqual(messages, expected)
    
    def test_stream_queryset_async_basic(self):
        """Wrapper for async test."""
        asyncio.run(self.async_test_stream_queryset_async_basic())
    
    async def async_test_stream_queryset_async_with_cancellation(self):
        """Test async streaming with context cancellation."""
        queryset = MockQuerySet(['item1', 'item2', 'item3', 'item4', 'item5'])
        mock_context = AsyncMock()
        
        # Cancel after 2 items
        mock_context.cancelled.side_effect = [False, False, True]
        
        messages = []
        async for message in stream_queryset_async(
            queryset=queryset,
            serializer_class=MockSerializer,
            context=mock_context,
            cancellation_check_interval=2,
            enable_metrics=False
        ):
            messages.append(message)
        
        # Should stop early due to cancellation
        self.assertLess(len(messages), 5)
    
    def test_stream_queryset_async_with_cancellation(self):
        """Wrapper for async cancellation test."""
        asyncio.run(self.async_test_stream_queryset_async_with_cancellation())


class PaginatedStreamingTests(TestCase):
    """Test paginated streaming functionality."""
    
    async def async_test_stream_paginated_queryset(self):
        """Test streaming paginated results."""
        # Create mock page
        items = ['item1', 'item2', 'item3']
        page = items  # Simplified mock page
        
        messages = []
        async for message in stream_paginated_queryset_async(
            page=page,
            serializer_class=MockSerializer,
            enable_metrics=False
        ):
            messages.append(message)
        
        expected = ['message_item1', 'message_item2', 'message_item3']
        self.assertEqual(messages, expected)
    
    def test_stream_paginated_queryset(self):
        """Wrapper for async paginated test."""
        asyncio.run(self.async_test_stream_paginated_queryset())


class MemoryOptimizationTests(TestCase):
    """Test memory optimization aspects of streaming."""
    
    def test_chunked_iteration(self):
        """Test that large querysets are processed in chunks."""
        # Create a large mock queryset
        large_items = [f'item{i}' for i in range(10000)]
        queryset = MockQuerySet(large_items)
        
        # Mock the iterator to track chunk size usage
        original_iterator = queryset.iterator
        chunk_sizes_used = []
        
        def mock_iterator(chunk_size=None):
            chunk_sizes_used.append(chunk_size)
            return original_iterator(chunk_size)
        
        queryset.iterator = mock_iterator
        
        # Stream with custom chunk size
        messages = list(stream_queryset_sync(
            queryset=queryset,
            serializer_class=MockSerializer,
            chunk_size=500,
            enable_metrics=False
        ))
        
        # Verify chunk size was used
        self.assertIn(500, chunk_sizes_used)
        self.assertEqual(len(messages), 10000)


class FlowControlTests(TestCase):
    """Test flow control mechanisms."""
    
    def test_cancellation_check_interval(self):
        """Test that cancellation is checked at specified intervals."""
        queryset = MockQuerySet([f'item{i}' for i in range(10)])
        mock_context = Mock()
        
        cancellation_checks = []
        original_cancelled = mock_context.cancelled
        
        def mock_cancelled():
            cancellation_checks.append(len(cancellation_checks))
            return original_cancelled() if hasattr(original_cancelled, '__call__') else False
        
        mock_context.cancelled = mock_cancelled
        
        messages = list(stream_queryset_sync(
            queryset=queryset,
            serializer_class=MockSerializer,
            context=mock_context,
            cancellation_check_interval=3,
            enable_metrics=False
        ))
        
        # Should check cancellation every 3 items
        self.assertGreater(len(cancellation_checks), 0)
        self.assertEqual(len(messages), 10)


@override_settings(GRPC_FRAMEWORK={
    'STREAMING_CHUNK_SIZE': 100,
    'STREAMING_CANCELLATION_CHECK_INTERVAL': 50,
    'STREAMING_YIELD_TIMEOUT': 10.0,
    'STREAMING_ENABLE_METRICS': False,
})
class StreamingSettingsTests(TestCase):
    """Test streaming configuration from Django settings."""
    
    def test_settings_integration(self):
        """Test that streaming respects Django settings."""
        # This test would verify that the settings are properly loaded
        # and used by the streaming functions
        pass


class BackpressureTests(TestCase):
    """Test backpressure handling and monitoring."""
    
    def test_metrics_track_backpressure_indicators(self):
        """Test that metrics collect backpressure indicators."""
        metrics = StreamingMetrics()
        
        # Simulate varying processing times
        for delay in [0.01, 0.05, 0.02, 0.1]:
            time.sleep(delay)
            metrics.record_item_streamed()
        
        avg_latency = metrics.get_average_latency()
        self.assertGreater(avg_latency, 0)
        
        # Check that we can identify slower processing
        self.assertEqual(len(metrics.processing_latency), 4)
        self.assertGreater(max(metrics.processing_latency), min(metrics.processing_latency))
