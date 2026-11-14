"""消息的构建规则。"""
from formatting import normalize_name


def create_message(name: str) -> dict:
    """将名字交给 normalize_name 清理，再构造包含问候文字与字符长度的结果。注意这里返回数据，JSON 输出由调用方处理。"""
    cleaned = normalize_name(name)
    return {"message": f"你好，{cleaned}", "name_length": len(cleaned)}

