#!/usr/bin/env python3
"""
参数记录器 - 用于测试 dispatcher 传参是否正确。
记录所有 sys.argv 到 /tmp/dispatch_params.log，输出 PUBLISHED。
"""
import sys
import datetime

log_path = "/tmp/dispatch_params.log"
ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

with open(log_path, "a") as f:
    f.write(f"[{ts}] {' '.join(sys.argv)}\n")
    f.write("  " + "\n  ".join(sys.argv[1:]) + "\n\n")

print("PUBLISHED scheduled_time=2099-01-01 00:00:00")
