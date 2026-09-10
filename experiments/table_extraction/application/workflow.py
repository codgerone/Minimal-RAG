from pathlib import Path
from typing import Callable
ToolExecutor = Callable[[Path], bool]

def run_all_extractors(
    pdf_path: Path,
    extractors: tuple[tuple[str, ToolExecutor], ...],
) -> list[str]:
    """顺序执行四种工具，单项失败后继续其余工具并汇总失败项。"""
    failures: list[str] = []
    for tool, extractor in extractors:
        try:
            succeeded = extractor(pdf_path)
        except Exception as error:  # 单个第三方工具异常不得阻止其余工具运行。
            failures.append(f"{tool}: {type(error).__name__}: {error}")
            continue
        if not succeeded:
            failures.append(f"{tool}: one or more strategies failed")
    return failures

