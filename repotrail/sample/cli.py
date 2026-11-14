"""Paperplane 的命令行入口。"""
import json
import sys

from service import create_message


def main():
    """接收命令行名字，调用 create_message 构建消息，再将结果输出为 JSON。先观察输入如何跨越 cli.py 与 service.py。"""
    name = sys.argv[1] if len(sys.argv) > 1 else "reader"
    message = create_message(name)
    print(json.dumps(message, ensure_ascii=False))


if __name__ == "__main__":
    main()

