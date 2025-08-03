# Enhanced Streaming & Flow Control

## Overview of Improvements

The enhanced streaming implementation addresses several critical issues:

1. **Memory Efficiency**: Uses Django QuerySet `iterator()` to avoid loading large datasets into memory
2. **Flow Control**: Implements proper async yielding patterns with context-aware batching
3. **Cancellation Support**: Checks for client cancellation periodically during streaming
4. **Backpressure Monitoring**: Collects metrics to help diagnose performance bottlenecks
5. **Timeout Handling**: Implements timeout patterns to avoid blocking indefinitely

## Configuration

Add streaming configuration to your Django settings:

```python
# settings.py
GRPC_FRAMEWORK = {
    # ... other settings ...

    # Streaming configuration
    'STREAMING_CHUNK_SIZE': 1000,  # Default chunk size for QuerySet iterator()
    'STREAMING_CANCELLATION_CHECK_INTERVAL': 100,  # Items before checking cancellation
    'STREAMING_YIELD_TIMEOUT': 30.0,  # Timeout in seconds between yields
    'STREAMING_ENABLE_METRICS': True,  # Enable metrics collection
}
```

## Basic Usage

The enhanced streaming is automatically used when you use `StreamModelMixin` or `AsyncStreamModelMixin`:

```python
# services.py
from django_socio_grpc import generics, mixins

class BookStreamService(mixins.AsyncStreamModelMixin, generics.GenericService):
    queryset = Book.objects.all().order_by('id')
    serializer_class = BookProtoSerializer
    pagination_class = PageNumberPagination

    # The Stream method automatically uses enhanced streaming
```

## Memory-Efficient Streaming

The enhanced implementation uses Django's QuerySet `iterator()` method to process large datasets without loading everything into memory:

```python
# Before (loads entire queryset into memory)
queryset = Book.objects.all()  # Could be millions of records
serializer = BookSerializer(queryset, many=True, stream=True)
yield from serializer.message  # All loaded into memory at once

# After (memory-efficient streaming)
queryset = Book.objects.all()
for item in queryset.iterator(chunk_size=1000):  # Processes in chunks
    serializer = BookSerializer(item, stream=True)
    yield serializer.message  # One item at a time
```

## Flow Control & Cancellation

The enhanced streaming checks for client cancellation periodically:

```python
async def Stream(self, request, context):
    async for message in stream_queryset_async(
        queryset=self.get_queryset(),
        serializer_class=self.get_serializer_class(),
        context=context,  # Passed for cancellation checking
        cancellation_check_interval=100  # Check every 100 items
    ):
        yield message
```

## Backpressure Monitoring

When metrics are enabled, the streaming operations collect performance data:

```python
# Example log output
[INFO] Streaming metrics: 10000 items, rate: 1250.50 items/s, avg latency: 0.0008s
```

This helps identify:
- Slow streaming rates indicating client or network issues
- High average latency suggesting serialization bottlenecks
- Items per second to understand throughput

## Custom Streaming

You can also use the streaming utilities directly for custom implementations:

```python
from django_socio_grpc.streaming import stream_queryset_async

class CustomService(generics.GenericService):

    @grpc_action(response_stream=True)
    async def CustomStream(self, request, context):
        queryset = MyModel.objects.filter(active=True)

        async for message in stream_queryset_async(
            queryset=queryset,
            serializer_class=MyModelSerializer,
            context=context,
            chunk_size=500,  # Custom chunk size
            cancellation_check_interval=50,  # More frequent checks
            enable_metrics=True
        ):
            yield message
```

## Error Handling

The enhanced streaming includes proper error handling:

```python
async def Stream(self, request, context):
    try:
        async for message in stream_queryset_async(...):
            yield message
    except Exception as e:
        logger.error(f"Streaming error: {e}")
        # Metrics are logged automatically
        raise
```

## Performance Considerations

### Chunk Size Selection

Choose chunk size based on your data:
- **Small records (< 1KB)**: Use larger chunks (1000-5000)
- **Large records (> 10KB)**: Use smaller chunks (100-500)
- **Memory-constrained environments**: Use smaller chunks (100-1000)

### Cancellation Check Interval

Balance responsiveness vs. performance:
- **Interactive clients**: Check every 10-50 items
- **Batch processing**: Check every 100-1000 items
- **High-throughput**: Check every 1000+ items

### Database Optimization

Ensure your queries are optimized for streaming:

```python
class OptimizedStreamService(mixins.AsyncStreamModelMixin, generics.GenericService):
    queryset = Book.objects.select_related('author').prefetch_related('tags').order_by('id')
    #                      ^^^^^^^^^^^^^^^ ^^^^^^^^^^^^^^^^^^^^^ ^^^^^^^^^^^^^^
    #                      Reduce queries  Reduce queries        Enable iterator()
```

## Migration from Legacy Streaming

If you're upgrading from the previous streaming implementation:

### Before (legacy)
```python
async def Stream(self, request, context):
    queryset = self.get_queryset()
    serializer = self.get_serializer(queryset, many=True, stream=True)
    messages = await serializer.amessage
    for message in messages:  # All in memory
        yield message
```

### After (enhanced)
```python
# No changes needed! The mixin handles this automatically
# Or for custom implementations:
async def Stream(self, request, context):
    async for message in stream_queryset_async(
        queryset=self.get_queryset(),
        serializer_class=self.get_serializer_class(),
        context=context
    ):
        yield message  # Memory-efficient streaming
```

## Monitoring & Debugging

Enable detailed logging to monitor streaming performance:

```python
# settings.py
LOGGING = {
    'version': 1,
    'loggers': {
        'django_socio_grpc.streaming': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}
```

## Testing Streaming Services

Test your streaming services with the provided utilities:

```python
from django_socio_grpc.tests.test_streaming_flow_control import MockQuerySet, MockSerializer

class StreamingServiceTests(TestCase):

    async def test_stream_with_cancellation(self):
        queryset = MockQuerySet(['item1', 'item2', 'item3'])
        mock_context = Mock()
        mock_context.cancelled.side_effect = [False, True]  # Cancel after 1 item

        messages = []
        async for message in stream_queryset_async(
            queryset=queryset,
            serializer_class=MockSerializer,
            context=mock_context,
            cancellation_check_interval=1
        ):
            messages.append(message)

        self.assertEqual(len(messages), 1)  # Should stop early
```

## Troubleshooting

### High Memory Usage
- Reduce `STREAMING_CHUNK_SIZE`
- Check for inefficient QuerySet operations
- Ensure queries are ordered for proper iteration

### Slow Streaming
- Increase `STREAMING_CHUNK_SIZE`
- Optimize database queries with `select_related()`/`prefetch_related()`
- Check serializer performance

### Client Disconnections
- Reduce `STREAMING_CANCELLATION_CHECK_INTERVAL`
- Lower `STREAMING_YIELD_TIMEOUT`
- Monitor metrics for disconnection patterns

### Performance Bottlenecks
- Enable metrics with `STREAMING_ENABLE_METRICS`
- Monitor logs for latency and throughput data
- Profile serializer performance for complex objects
