"""用户输入的规范化规则。"""


def normalize_name(name: str) -> str:
    """去除名字两侧的空白，将空输入转换为 reader，并将名字长度限制在 64 个字符。核对这三个规则在返回值中的先后顺序。"""
    return (name.strip() or "reader")[:64]

