import argparse


def main():
    parser = argparse.ArgumentParser(description='ASC MCP 本地 stdio 服务')
    parser.add_argument('--timeout', type=float, default=180, help='单次分析超时秒数，默认 180')
    args = parser.parse_args()
    if not 0 < args.timeout <= 3600:
        parser.error('--timeout 必须大于 0 且不超过 3600 秒')
    from .server import create_server
    create_server(args.timeout).run(transport='stdio')


if __name__ == '__main__':
    main()
