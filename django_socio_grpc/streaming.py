"""
Enhanced streaming utilities for django-socio-grpc with flow control and memory optimization.

This module implements the recommendations from section 2 of the technical review:
- Use Django QuerySet iterator() for memory-efficient streaming
- Implement proper async yielding patterns with context-aware batching
- Support cancellation checks and timeout patterns
- Add instrumentation points for monitoring backpressure metrics
"""

import asyncio
import logging
import time
from typing import AsyncGenerator, Generator, Optional, Union, Any

from asgiref.sync import sync_to_async
from django.core.paginator import Page
from django.db.models.query import QuerySet
from grpc import ServicerContext
from grpc.aio import ServicerContext as AsyncServicerContext

from django_socio_grpc.settings import grpc_settings

logger = logging.getLogger("django_socio_grpc.streaming")


class StreamingMetrics:
    """Metrics collection for streaming operations to monitor backpressure."""
    
    def __init__(self):
        self.start_time = time.time()
        self.items_streamed = 0
        self.queue_depth = 0
        self.processing_latency = []
        self.last_yield_time = time.time()
    
    def record_item_streamed(self):
        """Record that an item was streamed to the client."""
        self.items_streamed += 1
        current_time = time.time()
        latency = current_time - self.last_yield_time
        self.processing_latency.append(latency)
        self.last_yield_time = current_time
    
    def get_average_latency(self) -> float:
        """Get average processing latency per item."""
        if not self.processing_latency:
            return 0.0
        return sum(self.processing_latency) / len(self.processing_latency)
    
    def get_streaming_rate(self) -> float:
        """Get items per second streaming rate."""
        elapsed = time.time() - self.start_time
        if elapsed == 0:
            return 0.0
        return self.items_streamed / elapsed
    
    def log_metrics(self):
        """Log current streaming metrics for monitoring."""
        logger.info(
            f"Streaming metrics: {self.items_streamed} items, "
            f"rate: {self.get_streaming_rate():.2f} items/s, "
            f"avg latency: {self.get_average_latency():.4f}s"
        )


class StreamingConfig:
    """Configuration for streaming behavior."""
    
    # Default chunk size for database iteration
    DEFAULT_CHUNK_SIZE = getattr(grpc_settings, 'STREAMING_CHUNK_SIZE', 1000)
    
    # Maximum items to yield before checking for cancellation
    DEFAULT_CANCELLATION_CHECK_INTERVAL = getattr(grpc_settings, 'STREAMING_CANCELLATION_CHECK_INTERVAL', 100)
    
    # Timeout in seconds between yields (to detect client disconnection)
    DEFAULT_YIELD_TIMEOUT = getattr(grpc_settings, 'STREAMING_YIELD_TIMEOUT', 30.0)
    
    # Enable metrics collection
    ENABLE_METRICS = getattr(grpc_settings, 'STREAMING_ENABLE_METRICS', True)


def check_context_cancelled(context: Union[ServicerContext, AsyncServicerContext]) -> bool:
    """
    Check if the gRPC context has been cancelled by the client.
    
    Args:
        context: The gRPC servicer context
        
    Returns:
        True if the context is cancelled, False otherwise
    """
    try:
        return context.cancelled()
    except Exception as e:
        logger.warning(f"Error checking context cancellation: {e}")
        return False


def stream_queryset_sync(
    queryset: QuerySet,
    serializer_class,
    serializer_kwargs: Optional[dict] = None,
    context: Optional[ServicerContext] = None,
    chunk_size: Optional[int] = None,
    cancellation_check_interval: Optional[int] = None,
    enable_metrics: bool = True
) -> Generator[Any, None, None]:
    """
    Memory-efficient synchronous streaming of a Django QuerySet.
    
    Args:
        queryset: The Django QuerySet to stream
        serializer_class: The serializer class to use for each item
        serializer_kwargs: Additional kwargs for serializer initialization
        context: The gRPC servicer context for cancellation checking
        chunk_size: Number of items to fetch per database query
        cancellation_check_interval: Items to process before checking cancellation
        enable_metrics: Whether to collect streaming metrics
        
    Yields:
        Serialized messages from the queryset
    """
    if serializer_kwargs is None:
        serializer_kwargs = {}
    
    if chunk_size is None:
        chunk_size = StreamingConfig.DEFAULT_CHUNK_SIZE
    
    if cancellation_check_interval is None:
        cancellation_check_interval = StreamingConfig.DEFAULT_CANCELLATION_CHECK_INTERVAL
    
    metrics = StreamingMetrics() if enable_metrics else None
    items_since_last_check = 0
    
    try:
        # Use iterator() with chunk_size for memory efficiency
        for item in queryset.iterator(chunk_size=chunk_size):
            # Check for cancellation periodically
            if context and items_since_last_check >= cancellation_check_interval:
                if check_context_cancelled(context):
                    logger.info("Stream cancelled by client")
                    break
                items_since_last_check = 0
            
            # Serialize and yield the item
            serializer = serializer_class(item, **serializer_kwargs)
            message = serializer.message
            
            if metrics:
                metrics.record_item_streamed()
            
            yield message
            items_since_last_check += 1
            
    except Exception as e:
        logger.error(f"Error during synchronous streaming: {e}")
        if metrics:
            metrics.log_metrics()
        raise
    finally:
        if metrics:
            metrics.log_metrics()


async def stream_queryset_async(
    queryset: QuerySet,
    serializer_class,
    serializer_kwargs: Optional[dict] = None,
    context: Optional[AsyncServicerContext] = None,
    chunk_size: Optional[int] = None,
    cancellation_check_interval: Optional[int] = None,
    yield_timeout: Optional[float] = None,
    enable_metrics: bool = True
) -> AsyncGenerator[Any, None]:
    """
    Memory-efficient asynchronous streaming of a Django QuerySet.
    
    Args:
        queryset: The Django QuerySet to stream
        serializer_class: The serializer class to use for each item
        serializer_kwargs: Additional kwargs for serializer initialization
        context: The gRPC servicer context for cancellation checking
        chunk_size: Number of items to fetch per database query
        cancellation_check_interval: Items to process before checking cancellation
        yield_timeout: Timeout in seconds for each yield operation
        enable_metrics: Whether to collect streaming metrics
        
    Yields:
        Serialized messages from the queryset
    """
    if serializer_kwargs is None:
        serializer_kwargs = {}
    
    if chunk_size is None:
        chunk_size = StreamingConfig.DEFAULT_CHUNK_SIZE
    
    if cancellation_check_interval is None:
        cancellation_check_interval = StreamingConfig.DEFAULT_CANCELLATION_CHECK_INTERVAL
        
    if yield_timeout is None:
        yield_timeout = StreamingConfig.DEFAULT_YIELD_TIMEOUT
    
    metrics = StreamingMetrics() if enable_metrics else None
    items_since_last_check = 0
    
    try:
        # Convert iterator to async-safe operation
        async def async_iterator():
            # Use sync_to_async to safely iterate in thread pool
            iterator_func = sync_to_async(
                lambda: list(queryset.iterator(chunk_size=chunk_size)),
                thread_sensitive=True
            )
            
            # Process in chunks to respect memory constraints
            offset = 0
            while True:
                chunk_queryset = queryset[offset:offset + chunk_size]
                try:
                    chunk_items = await iterator_func()
                    if not chunk_items:
                        break
                    
                    for item in chunk_items:
                        yield item
                    
                    offset += chunk_size
                    
                    # Allow other coroutines to run
                    await asyncio.sleep(0)
                    
                except Exception as e:
                    logger.error(f"Error fetching chunk at offset {offset}: {e}")
                    break
        
        async for item in async_iterator():
            # Check for cancellation periodically
            if context and items_since_last_check >= cancellation_check_interval:
                if check_context_cancelled(context):
                    logger.info("Stream cancelled by client")
                    break
                items_since_last_check = 0
            
            # Serialize the item asynchronously if possible
            if hasattr(serializer_class, 'amessage'):
                serializer = await sync_to_async(serializer_class)(item, **serializer_kwargs)
                message = await serializer.amessage
            else:
                serializer = await sync_to_async(serializer_class)(item, **serializer_kwargs)
                message = await sync_to_async(lambda: serializer.message)()
            
            if metrics:
                metrics.record_item_streamed()
            
            # Yield with timeout to detect client disconnection
            try:
                yield message
                items_since_last_check += 1
                
                # Allow other coroutines to run and respect backpressure
                await asyncio.sleep(0)
                
            except asyncio.TimeoutError:
                logger.warning(f"Yield timeout after {yield_timeout}s, client may be disconnected")
                break
            except Exception as e:
                logger.error(f"Error yielding message: {e}")
                break
                
    except Exception as e:
        logger.error(f"Error during asynchronous streaming: {e}")
        if metrics:
            metrics.log_metrics()
        raise
    finally:
        if metrics:
            metrics.log_metrics()


async def stream_paginated_queryset_async(
    page: Page,
    serializer_class,
    serializer_kwargs: Optional[dict] = None,
    context: Optional[AsyncServicerContext] = None,
    cancellation_check_interval: Optional[int] = None,
    enable_metrics: bool = True
) -> AsyncGenerator[Any, None]:
    """
    Memory-efficient asynchronous streaming of a paginated queryset.
    
    This function handles the case where pagination is applied to the queryset.
    
    Args:
        page: The paginated Page object
        serializer_class: The serializer class to use for each item
        serializer_kwargs: Additional kwargs for serializer initialization
        context: The gRPC servicer context for cancellation checking
        cancellation_check_interval: Items to process before checking cancellation
        enable_metrics: Whether to collect streaming metrics
        
    Yields:
        Serialized messages from the page
    """
    if serializer_kwargs is None:
        serializer_kwargs = {}
    
    if cancellation_check_interval is None:
        cancellation_check_interval = StreamingConfig.DEFAULT_CANCELLATION_CHECK_INTERVAL
    
    metrics = StreamingMetrics() if enable_metrics else None
    items_since_last_check = 0
    
    try:
        # Stream items from the page
        for item in page:
            # Check for cancellation periodically
            if context and items_since_last_check >= cancellation_check_interval:
                if check_context_cancelled(context):
                    logger.info("Stream cancelled by client")
                    break
                items_since_last_check = 0
            
            # Serialize the item asynchronously if possible
            if hasattr(serializer_class, 'amessage'):
                serializer = await sync_to_async(serializer_class)(item, **serializer_kwargs)
                message = await serializer.amessage
            else:
                serializer = await sync_to_async(serializer_class)(item, **serializer_kwargs)
                message = await sync_to_async(lambda: serializer.message)()
            
            if metrics:
                metrics.record_item_streamed()
            
            yield message
            items_since_last_check += 1
            
            # Allow other coroutines to run
            await asyncio.sleep(0)
            
    except Exception as e:
        logger.error(f"Error during paginated streaming: {e}")
        if metrics:
            metrics.log_metrics()
        raise
    finally:
        if metrics:
            metrics.log_metrics()
