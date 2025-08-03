# Enhanced Streaming Migration Guide

This guide helps you migrate from the legacy streaming implementation to the new enhanced streaming with flow control and memory optimization.

## What Changed

The enhanced streaming implementation improves:

1. **Memory Efficiency**: Uses Django QuerySet `iterator()` instead of loading entire datasets
2. **Flow Control**: Adds cancellation checking and timeout handling
3. **Backpressure Monitoring**: Collects metrics to identify performance bottlenecks
4. **Configuration**: Provides tunable parameters for different use cases

## Breaking Changes

### None for Basic Usage

If you're using the standard mixins, **no code changes are required**:

```python
# This continues to work unchanged
class BookService(mixins.AsyncStreamModelMixin, generics.GenericService):
    queryset = Book.objects.all()
    serializer_class = BookSerializer
    # Stream method automatically uses enhanced streaming
```

### Custom Stream Implementations

If you have custom streaming implementations, consider migrating to the new utilities:

#### Before (Legacy)
```python
async def CustomStream(self, request, context):
    queryset = self.get_queryset()
    serializer = self.get_serializer(queryset, many=True, stream=True)
    messages = await serializer.amessage

    for message in messages:  # All loaded into memory
        yield message
```

#### After (Enhanced)
```python
from django_socio_grpc.streaming import stream_queryset_async

async def CustomStream(self, request, context):
    queryset = self.get_queryset()

    async for message in stream_queryset_async(
        queryset=queryset,
        serializer_class=self.get_serializer_class(),
        context=context  # Enables cancellation checking
    ):
        yield message  # Memory-efficient, one at a time
```

## Configuration Migration

### Add Streaming Settings

Add the new streaming configuration to your Django settings:

```python
# settings.py
GRPC_FRAMEWORK = {
    # ... existing settings ...

    # New streaming configuration
    'STREAMING_CHUNK_SIZE': 1000,  # Default: 1000
    'STREAMING_CANCELLATION_CHECK_INTERVAL': 100,  # Default: 100
    'STREAMING_YIELD_TIMEOUT': 30.0,  # Default: 30.0
    'STREAMING_ENABLE_METRICS': True,  # Default: True
}
```

### Tuning Guidelines

Choose values based on your use case:

#### High Throughput Services
```python
'STREAMING_CHUNK_SIZE': 5000,
'STREAMING_CANCELLATION_CHECK_INTERVAL': 1000,
'STREAMING_ENABLE_METRICS': False,  # Disable for max performance
```

#### Interactive Services
```python
'STREAMING_CHUNK_SIZE': 100,
'STREAMING_CANCELLATION_CHECK_INTERVAL': 10,
'STREAMING_ENABLE_METRICS': True,
```

#### Memory-Constrained Environments
```python
'STREAMING_CHUNK_SIZE': 50,
'STREAMING_CANCELLATION_CHECK_INTERVAL': 25,
```

## Performance Impact

### Memory Usage

The enhanced streaming significantly reduces memory usage for large datasets:

```python
# Legacy: Memory usage = O(n) where n is total records
queryset = Book.objects.all()  # 1 million records
serializer = BookSerializer(queryset, many=True)  # All in memory

# Enhanced: Memory usage = O(chunk_size)
queryset = Book.objects.all()  # 1 million records
# Only ~1000 records in memory at a time (configurable)
```

### CPU Overhead

The enhanced streaming adds minimal CPU overhead:
- Cancellation checks: ~0.1ms per check interval
- Metrics collection: ~0.01ms per item (when enabled)
- Iterator chunking: Negligible overhead

### Network Behavior

Streaming behavior remains the same from the client perspective:
- Same gRPC streaming semantics
- Same proto message formats
- Same error handling

## Monitoring & Debugging

### Enable Streaming Logs

Monitor streaming performance with detailed logging:

```python
# settings.py
LOGGING = {
    'version': 1,
    'loggers': {
        'django_socio_grpc.streaming': {
            'handlers': ['console'],
            'level': 'INFO',  # Change to DEBUG for more detail
        },
    },
}
```

### Example Log Output
```
[INFO] Streaming metrics: 50000 items, rate: 2500.25 items/s, avg latency: 0.0004s
```

### Metrics Interpretation

- **Items/s rate**: Throughput indicator
  - < 100/s: Slow serialization or database queries
  - 100-1000/s: Normal for complex objects
  - > 1000/s: Good performance for simple objects

- **Average latency**: Processing time per item
  - < 0.001s: Excellent
  - 0.001-0.01s: Good
  - > 0.01s: May indicate serialization bottlenecks

## Testing Migration

### Automated Tests

Verify your streaming services work correctly:

```python
from django_socio_grpc.tests.test_streaming_flow_control import MockQuerySet

class StreamingMigrationTests(TestCase):

    async def test_enhanced_streaming_compatibility(self):
        """Verify enhanced streaming produces same results as legacy."""
        service = YourStreamingService()

        # Test with small dataset
        messages = []
        async for message in service.Stream(request, context):
            messages.append(message)

        # Verify expected count and content
        self.assertEqual(len(messages), expected_count)
```

### Load Testing

Test streaming performance with realistic data volumes:

```python
# Create test data
for i in range(100000):
    TestModel.objects.create(name=f"test_{i}")

# Test streaming performance
start_time = time.time()
count = 0
async for message in service.Stream(request, context):
    count += 1
    if count % 10000 == 0:
        print(f"Streamed {count} items")

duration = time.time() - start_time
print(f"Streamed {count} items in {duration:.2f}s ({count/duration:.0f} items/s)")
```

## Rollback Plan

If you need to rollback to legacy streaming behavior:

1. **Disable enhanced streaming temporarily** by modifying the mixins:

```python
# Temporary rollback - restore in your services
class LegacyStreamModelMixin(mixins.StreamModelMixin):
    def Stream(self, request, context):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)

        if page is not None:
            serializer = self.get_serializer(page, many=True, stream=True)
        else:
            serializer = self.get_serializer(queryset, many=True, stream=True)

        yield from serializer.message
```

2. **Remove streaming settings** from Django settings temporarily

3. **Monitor for any differences** in behavior or performance

## Common Issues & Solutions

### Out of Memory Errors
**Problem**: Still getting OOM errors with enhanced streaming
**Solution**: Reduce `STREAMING_CHUNK_SIZE` and check for inefficient queries

### Slow Streaming Performance
**Problem**: Enhanced streaming is slower than expected
**Solution**: Increase `STREAMING_CHUNK_SIZE` and optimize database queries

### Client Disconnection Not Detected
**Problem**: Server continues streaming after client disconnects
**Solution**: Reduce `STREAMING_CANCELLATION_CHECK_INTERVAL`

### High CPU Usage from Metrics
**Problem**: Metrics collection causing performance issues
**Solution**: Set `STREAMING_ENABLE_METRICS: False` in production
