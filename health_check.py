#!/usr/bin/env python3
"""
健康检查脚本
用于 Docker 健康检查和外部监控
"""

import sys
import urllib.request
import urllib.error


def check_health():
    """检查服务健康状态"""
    try:
        url = "http://localhost:8000/health"
        req = urllib.request.Request(url)

        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status == 200:
                data = response.read().decode('utf-8')
                print(f"Health check passed: {data}")
                return 0
            else:
                print(f"Health check failed: HTTP {response.status}")
                return 1

    except urllib.error.URLError as e:
        print(f"Health check failed: {e}")
        return 1
    except Exception as e:
        print(f"Health check error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(check_health())
