"""Benchmark: context compression levels."""
import time
from pathlib import Path
from llm_code.api.types import Message, TextBlock, ToolResultBlock
from llm_code.runtime.session import Session
from llm_code.runtime.compressor import ContextCompressor


def make_large_session(n_messages: int, result_size: int = 5000) -> Session:
    session = Session.create(project_path=Path("/tmp"))
    for i in range(n_messages):
        if i % 3 == 0:
            msg = Message(role="user", content=(TextBlock(text=f"Do task {i}"),))
        elif i % 3 == 1:
            msg = Message(role="assistant", content=(TextBlock(text=f"I'll use read_file for task {i}"),))
        else:
            msg = Message(role="user", content=(ToolResultBlock(tool_use_id=f"t{i}", content="x" * result_size),))
        session = session.add_message(msg)
    return session


def bench_compression():
    compressor = ContextCompressor(max_result_chars=2000)
    sizes = [10, 50, 100, 200]

    print(f"{'Messages':>10} {'Before':>10} {'After':>10} {'Time':>10} {'Levels':>10}")
    print("-" * 55)

    for n in sizes:
        session = make_large_session(n, result_size=5000)
        before = session.estimated_tokens()
        start = time.perf_counter()
        compressed = compressor.compress(session, max_tokens=10000)
        elapsed = time.perf_counter() - start
        after = compressed.estimated_tokens()
        ratio = f"{after/before:.0%}" if before > 0 else "N/A"
        print(f"{n:>10} {before:>10,} {after:>10,} {elapsed:>9.3f}s {ratio:>10}")


if __name__ == "__main__":
    bench_compression()
