"""格式化工具（占位，可后续扩展）。"""


def format_gee_code(code: str, language: str = "python") -> str:
    """格式化 GEE 代码片段用于展示。"""
    return f"```{language}\n{code}\n```"
