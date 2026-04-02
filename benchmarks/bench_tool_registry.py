"""Benchmark: tool registry lookup and execution."""
import time
from llm_code.tools.registry import ToolRegistry
from llm_code.tools.read_file import ReadFileTool
from llm_code.tools.write_file import WriteFileTool
from llm_code.tools.bash import BashTool
from llm_code.tools.glob_search import GlobSearchTool
from llm_code.tools.grep_search import GrepSearchTool
from llm_code.tools.edit_file import EditFileTool


def bench_registry():
    registry = ToolRegistry()
    for cls in (ReadFileTool, WriteFileTool, EditFileTool, BashTool, GlobSearchTool, GrepSearchTool):
        registry.register(cls())

    # Benchmark lookup
    n = 10000
    start = time.perf_counter()
    for _ in range(n):
        registry.get("read_file")
        registry.get("bash")
        registry.get("nonexistent")
    elapsed = time.perf_counter() - start
    print(f"Registry lookup: {n*3:,} lookups in {elapsed:.3f}s ({n*3/elapsed:,.0f} ops/sec)")

    # Benchmark definitions
    start = time.perf_counter()
    for _ in range(n):
        registry.definitions()
    elapsed = time.perf_counter() - start
    print(f"Definitions: {n:,} calls in {elapsed:.3f}s ({n/elapsed:,.0f} ops/sec)")


if __name__ == "__main__":
    bench_registry()
