#!/usr/bin/env python3
"""
server.py 测试套件
测试项目：
- 接口/协议测试：验证各路由的正确性、错误处理、协议格式
- 压力测试：并发请求下的稳定性、响应时间、成功率
- 单元测试：各路由函数的边界条件和业务逻辑
运行方式：
    python test_server.py [--host HOST] [--port PORT] [--test TYPE]
    TYPE: protocol, stress, unit, all (默认all)
生成报告文件: test_report_YYYYMMDD_HHMMSS.txt
"""

import socket
import struct
import json
import time
import threading
import sys
import os
import random
import string
import traceback
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from typing import Dict, List, Any, Tuple, Optional

# 配置
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
REQUEST_TIMEOUT = 10  # 秒
CONNECTION_TIMEOUT = 5

# 测试账号（需要预先存在或动态注册）
TEST_USERNAME = "test_user_" + ''.join(random.choices(string.ascii_lowercase, k=6))
TEST_PASSWORD = "TestPass123"
TEST_EMAIL = f"test_{random.randint(1000,9999)}@test.com"
TEST_PHONE = "138" + ''.join(random.choices(string.digits, k=8))
TEST_PLAYERNAME = "TestPlayer_" + ''.join(random.choices(string.ascii_letters, k=5))


class ServerClient:
    """TCP JSON 客户端，用于与服务端通信"""
    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT):
        self.host = host
        self.port = port
        self.sock = None
        self.seq = 0

    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(CONNECTION_TIMEOUT)
        self.sock.connect((self.host, self.port))

    def disconnect(self):
        if self.sock:
            self.sock.close()
            self.sock = None

    def _pack(self, msg: dict) -> bytes:
        body = json.dumps(msg, ensure_ascii=False).encode('utf-8')
        return struct.pack('>I', len(body)) + body

    def _recv_exact(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionResetError
            buf.extend(chunk)
        return bytes(buf)

    def send_request(self, req_type: str, data: dict, timeout=REQUEST_TIMEOUT) -> dict:
        """发送请求并返回响应"""
        self.seq += 1
        req = {"type": req_type, "seq": self.seq}
        req.update(data)
        # 确保每次都使用新连接（压力测试除外）
        self.connect()
        try:
            self.sock.settimeout(timeout)
            self.sock.sendall(self._pack(req))
            len_bs = self._recv_exact(4)
            body_len = struct.unpack('>I', len_bs)[0]
            body = self._recv_exact(body_len)
            resp = json.loads(body.decode('utf-8'))
            return resp
        finally:
            self.disconnect()

    def send_request_raw(self, req_type: str, data: dict, timeout=REQUEST_TIMEOUT) -> dict:
        """使用现有连接发送请求（用于压力测试复用连接）"""
        self.seq += 1
        req = {"type": req_type, "seq": self.seq}
        req.update(data)
        self.sock.settimeout(timeout)
        self.sock.sendall(self._pack(req))
        len_bs = self._recv_exact(4)
        body_len = struct.unpack('>I', len_bs)[0]
        body = self._recv_exact(body_len)
        return json.loads(body.decode('utf-8'))


class TestReport:
    """测试报告收集与生成"""
    def __init__(self):
        self.start_time = datetime.now()
        self.results = []  # List of (test_name, passed, message, duration)
        self.stats = defaultdict(lambda: {"passed": 0, "failed": 0, "errors": []})
        self.pressure_results = {}

    def add_result(self, test_name: str, passed: bool, message: str = "", duration: float = 0):
        category = test_name.split('.')[0] if '.' in test_name else "general"
        self.results.append((test_name, passed, message, duration))
        if passed:
            self.stats[category]["passed"] += 1
        else:
            self.stats[category]["failed"] += 1
            self.stats[category]["errors"].append(f"{test_name}: {message}")

    def add_pressure_result(self, name: str, total: int, success: int, failed: int, avg_time: float, max_time: float, min_time: float, qps: float):
        self.pressure_results[name] = {
            "total": total, "success": success, "failed": failed,
            "avg_time": avg_time, "max_time": max_time, "min_time": min_time, "qps": qps
        }

    def generate(self, filename: str = None) -> str:
        end_time = datetime.now()
        duration = (end_time - self.start_time).total_seconds()
        total_tests = len(self.results)
        passed = sum(1 for _, p, _, _ in self.results if p)
        failed = total_tests - passed

        lines = []
        lines.append("=" * 80)
        lines.append(f"测试报告 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("=" * 80)
        lines.append(f"测试耗时: {duration:.2f} 秒")
        lines.append(f"测试总数: {total_tests}")
        lines.append(f"通过: {passed}, 失败: {failed}")
        lines.append("")

        # 按类别统计
        lines.append("--- 按类别统计 ---")
        for cat, stat in self.stats.items():
            total_cat = stat["passed"] + stat["failed"]
            pass_rate = (stat["passed"] / total_cat * 100) if total_cat > 0 else 0
            lines.append(f"  {cat}: 通过 {stat['passed']}/{total_cat} ({pass_rate:.1f}%)")
            if stat["errors"]:
                for err in stat["errors"][:5]:  # 只显示前5个错误
                    lines.append(f"    - {err}")
        lines.append("")

        # 详细结果
        lines.append("--- 详细测试结果 ---")
        for name, passed, msg, dur in self.results:
            status = "✅ PASS" if passed else "❌ FAIL"
            lines.append(f"{status} | {name} | {dur:.3f}s")
            if msg and not passed:
                lines.append(f"      原因: {msg}")
        lines.append("")

        # 压力测试结果
        if self.pressure_results:
            lines.append("--- 压力测试结果 ---")
            for name, pr in self.pressure_results.items():
                lines.append(f"  {name}:")
                lines.append(f"    总请求: {pr['total']}, 成功: {pr['success']}, 失败: {pr['failed']}")
                lines.append(f"    QPS: {pr['qps']:.2f}")
                lines.append(f"    响应时间(秒) - 平均: {pr['avg_time']:.3f}, 最小: {pr['min_time']:.3f}, 最大: {pr['max_time']:.3f}")
        lines.append("")

        lines.append("=" * 80)
        report_text = "\n".join(lines)

        if filename is None:
            filename = f"test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(report_text)
        print(f"报告已保存至: {filename}")
        return report_text


class TestSuite:
    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT):
        self.host = host
        self.port = port
        self.report = TestReport()
        self.test_user_id = None
        self.admin_user_id = 1  # 假设管理员ID为1，需要密码验证
        self.admin_password = "admin123"  # 根据实际情况修改

    def run_all(self):
        """运行所有测试"""
        print("开始运行完整测试套件...")
        self.test_protocol()
        self.test_unit()
        self.test_stress()
        report_text = self.report.generate()
        print("\n" + report_text)

    def test_protocol(self):
        """接口/协议测试"""
        print("\n>>> 开始协议测试...")
        client = ServerClient(self.host, self.port)

        # 辅助函数
        def test(name, req_type, data, expected_success=True, expected_key=None):
            start = time.time()
            try:
                resp = client.send_request(req_type, data)
                dur = time.time() - start
                success = resp.get("success") == expected_success
                if expected_key:
                    success = success and expected_key in resp
                msg = resp.get("message", "")
                self.report.add_result(f"protocol.{name}", success, msg, dur)
                return resp
            except Exception as e:
                dur = time.time() - start
                self.report.add_result(f"protocol.{name}", False, str(e), dur)
                return None

        # 1. 注册新用户
        reg_data = {
            "username": TEST_USERNAME,
            "password": TEST_PASSWORD,
            "nickname": "TestNick",
            "email": TEST_EMAIL,
            "phone": TEST_PHONE,
            "playername": TEST_PLAYERNAME
        }
        resp = test("register_new_user", "register", reg_data, True)
        if resp and resp.get("success"):
            # 获取用户ID
            login_resp = client.send_request("login", {"username": TEST_USERNAME, "password": TEST_PASSWORD})
            if login_resp.get("success"):
                self.test_user_id = login_resp["user"]["UserID"]

        # 2. 重复注册（应失败）
        test("register_duplicate_username", "register", reg_data, False)

        # 3. 登录正确密码
        test("login_correct", "login", {"username": TEST_USERNAME, "password": TEST_PASSWORD}, True)

        # 4. 登录错误密码
        test("login_wrong_password", "login", {"username": TEST_USERNAME, "password": "wrongpass"}, False)

        # 5. 获取个人信息
        if self.test_user_id:
            test("profile_get", "profile", {"user_id": self.test_user_id}, True, "user")

        # 6. 签到
        if self.test_user_id:
            # 可能已签到，预期可能失败
            test("sign_check", "sign", {"user_id": self.test_user_id}, None)  # 不检查success，只检查协议

        # 7. 排行榜
        test("leaderboard", "leaderboard", {}, True, "coin")

        # 8. 获取联系人列表
        if self.test_user_id:
            test("get_contacts", "get_contacts", {"user_id": self.test_user_id}, True, "contacts")

        # 9. 发送消息（给自己）
        if self.test_user_id:
            test("send_message", "send_message", {
                "sender_id": self.test_user_id,
                "receiver_id": self.test_user_id,
                "content": "测试消息"
            }, True)

        # 10. 获取未读消息
        if self.test_user_id:
            test("get_unread_messages", "get_unread_messages", {"user_id": self.test_user_id}, True)

        # 11. 更新个人资料
        if self.test_user_id:
            test("update_profile", "update_profile", {
                "user_id": self.test_user_id,
                "nickname": "UpdatedNick",
                "email": TEST_EMAIL,
                "phone": TEST_PHONE,
                "first_name": "Test",
                "last_name": "User",
                "gender": "男",
                "birthday": "2000-01-01",
                "bio": "Test bio"
            }, True)

        # 12. 无效请求类型
        test("invalid_type", "nonexistent_type", {}, False)

        # 13. 缺少必要字段
        test("missing_fields", "login", {}, False)

        print("协议测试完成。")

    def test_unit(self):
        """单元测试 - 测试各路由的业务逻辑边界情况"""
        print("\n>>> 开始单元测试...")
        client = ServerClient(self.host, self.port)

        def unit_test(name, req_type, data, validator):
            start = time.time()
            try:
                resp = client.send_request(req_type, data)
                dur = time.time() - start
                passed, msg = validator(resp)
                self.report.add_result(f"unit.{name}", passed, msg, dur)
            except Exception as e:
                self.report.add_result(f"unit.{name}", False, str(e), time.time() - start)

        # 确保有测试用户
        if not self.test_user_id:
            reg_resp = client.send_request("register", {
                "username": TEST_USERNAME,
                "password": TEST_PASSWORD,
                "nickname": "TestNick",
                "email": TEST_EMAIL,
                "phone": TEST_PHONE,
                "playername": TEST_PLAYERNAME
            })
            if reg_resp.get("success"):
                login_resp = client.send_request("login", {"username": TEST_USERNAME, "password": TEST_PASSWORD})
                if login_resp.get("success"):
                    self.test_user_id = login_resp["user"]["UserID"]

        uid = self.test_user_id

        # 测试用例
        # 1. 注册邮箱格式验证
        unit_test("register_invalid_email", "register", {
            "username": "invalidemail",
            "password": "pass123",
            "nickname": "Test",
            "email": "notanemail",
            "phone": "13800138000",
            "playername": "player"
        }, lambda r: (r.get("success") is False and "邮箱" in r.get("message", ""), "邮箱格式验证"))

        # 2. 注册手机号格式验证
        unit_test("register_invalid_phone", "register", {
            "username": "invalidphone",
            "password": "pass123",
            "nickname": "Test",
            "email": "test@test.com",
            "phone": "123",
            "playername": "player"
        }, lambda r: (r.get("success") is False and "手机" in r.get("message", ""), "手机号格式验证"))

        # 3. 发送超长消息
        if uid:
            long_content = "A" * 300
            unit_test("send_long_message", "send_message", {
                "sender_id": uid, "receiver_id": uid, "content": long_content
            }, lambda r: (r.get("success") is True, "超长消息应截断但成功"))

        # 4. 赠与金币超过限额
        if uid:
            # 先赠送到上限
            for _ in range(6):
                client.send_request("give_gift", {"sender_id": uid, "receiver_id": uid, "gift_type": "coin"})
            unit_test("give_gift_exceed_limit", "give_gift", {
                "sender_id": uid, "receiver_id": uid, "gift_type": "coin"
            }, lambda r: (r.get("success") is False and "上限" in r.get("message", ""), "赠与上限检查"))

        # 5. 获取不存在的用户资料
        unit_test("get_nonexistent_user", "get_user_profile", {
            "user_id": uid, "target_id": 99999
        }, lambda r: (r.get("success") is False, "不存在的用户应返回失败"))

        # 6. 权限不足的操作（尝试修改他人权限，非管理员）
        if uid:
            unit_test("insufficient_permission", "update_role", {
                "user_id": uid, "role_id": 1
            }, lambda r: (r.get("success") is False, "普通用户不能修改权限"))

        # 7. 白名单申请重复提交
        if uid:
            # 先提交一次
            client.send_request("whitelist_apply", {
                "user_id": uid, "playername": TEST_PLAYERNAME, "genuine": 1, "reason": "test"
            })
            unit_test("duplicate_whitelist_apply", "whitelist_apply", {
                "user_id": uid, "playername": TEST_PLAYERNAME, "genuine": 1, "reason": "test"
            }, lambda r: (r.get("success") is False and "未审核" in r.get("message", ""), "重复申请应被拒绝"))

        # 8. 添加联系人自己
        if uid:
            unit_test("add_self_contact", "add_contact", {
                "user_id": uid, "contact_id": uid
            }, lambda r: (r.get("success") is False, "不能添加自己为联系人"))

        print("单元测试完成。")

    def test_stress(self):
        """压力测试 - 并发请求（使用长连接复用）"""
        print("\n>>> 开始压力测试...")
        if not self.test_user_id:
            print("警告：无测试用户，跳过需要登录的压力测试")
            return

        # 测试配置（降低并发以适应服务端能力）
        CONCURRENT_USERS = 10  # 10 并发
        REQUESTS_PER_USER = 50
        TOTAL_REQUESTS = CONCURRENT_USERS * REQUESTS_PER_USER

        # ---------- 登录压力测试（每个请求独立连接，测试连接建立能力） ----------
        def login_worker(worker_id):
            client = ServerClient(self.host, self.port)
            results = []
            for i in range(REQUESTS_PER_USER):
                start = time.time()
                try:
                    # 登录请求每次独立连接
                    resp = client.send_request("login", {
                        "username": TEST_USERNAME,
                        "password": TEST_PASSWORD
                    })
                    dur = time.time() - start
                    results.append((resp.get("success", False), dur))
                except Exception as e:
                    results.append((False, time.time() - start))
                    # 如果连接失败，重置客户端以便下次重新连接
                    client.disconnect()
            return results

        print(f"压力测试1: 登录接口 - {CONCURRENT_USERS}并发, 每用户{REQUESTS_PER_USER}请求")
        start_time = time.time()
        with ThreadPoolExecutor(max_workers=CONCURRENT_USERS) as executor:
            futures = [executor.submit(login_worker, i) for i in range(CONCURRENT_USERS)]
            all_results = []
            for f in as_completed(futures):
                all_results.extend(f.result())
        total_time = time.time() - start_time

        success_count = sum(1 for s, _ in all_results if s)
        fail_count = len(all_results) - success_count
        durations = [d for _, d in all_results if d > 0]
        if durations:
            avg_dur = sum(durations) / len(durations)
            max_dur = max(durations)
            min_dur = min(durations)
        else:
            avg_dur = max_dur = min_dur = 0
        qps = len(all_results) / total_time if total_time > 0 else 0

        self.report.add_pressure_result(
            "login_concurrent",
            len(all_results), success_count, fail_count,
            avg_dur, max_dur, min_dur, qps
        )

        # ---------- 获取个人信息压力测试（长连接复用） ----------
        def profile_worker(worker_id):
            client = ServerClient(self.host, self.port)
            results = []
            try:
                # 先登录获取 uid
                login_resp = client.send_request("login", {"username": TEST_USERNAME, "password": TEST_PASSWORD})
                if not login_resp.get("success"):
                    return [(False, 0)] * REQUESTS_PER_USER
                uid = login_resp["user"]["UserID"]

                # 建立长连接用于后续请求
                client.connect()
                for i in range(REQUESTS_PER_USER):
                    start = time.time()
                    try:
                        resp = client.send_request_raw("profile", {"user_id": uid})
                        dur = time.time() - start
                        results.append((resp.get("success", False), dur))
                    except Exception:
                        # 若连接断开，尝试重新连接并重试一次
                        try:
                            client.disconnect()
                            client.connect()
                            resp = client.send_request_raw("profile", {"user_id": uid})
                            dur = time.time() - start
                            results.append((resp.get("success", False), dur))
                        except:
                            results.append((False, time.time() - start))
            finally:
                client.disconnect()
            return results

        print(f"压力测试2: 获取个人信息 - {CONCURRENT_USERS}并发, 每用户{REQUESTS_PER_USER}请求 (长连接)")
        start_time = time.time()
        with ThreadPoolExecutor(max_workers=CONCURRENT_USERS) as executor:
            futures = [executor.submit(profile_worker, i) for i in range(CONCURRENT_USERS)]
            all_results = []
            for f in as_completed(futures):
                all_results.extend(f.result())
        total_time = time.time() - start_time

        success_count = sum(1 for s, _ in all_results if s)
        fail_count = len(all_results) - success_count
        durations = [d for _, d in all_results if d > 0]
        if durations:
            avg_dur = sum(durations) / len(durations)
            max_dur = max(durations)
            min_dur = min(durations)
        else:
            avg_dur = max_dur = min_dur = 0
        qps = len(all_results) / total_time if total_time > 0 else 0

        self.report.add_pressure_result(
            "profile_concurrent",
            len(all_results), success_count, fail_count,
            avg_dur, max_dur, min_dur, qps
        )

        # ---------- 发送消息压力测试（长连接复用） ----------
        def message_worker(worker_id):
            client = ServerClient(self.host, self.port)
            results = []
            try:
                login_resp = client.send_request("login", {"username": TEST_USERNAME, "password": TEST_PASSWORD})
                if not login_resp.get("success"):
                    return [(False, 0)] * REQUESTS_PER_USER
                uid = login_resp["user"]["UserID"]

                client.connect()
                for i in range(REQUESTS_PER_USER):
                    start = time.time()
                    try:
                        resp = client.send_request_raw("send_message", {
                            "sender_id": uid,
                            "receiver_id": uid,
                            "content": f"压力测试消息 {i}"
                        })
                        dur = time.time() - start
                        results.append((resp.get("success", False), dur))
                    except Exception:
                        try:
                            client.disconnect()
                            client.connect()
                            resp = client.send_request_raw("send_message", {
                                "sender_id": uid,
                                "receiver_id": uid,
                                "content": f"压力测试消息 {i} (重试)"
                            })
                            dur = time.time() - start
                            results.append((resp.get("success", False), dur))
                        except:
                            results.append((False, time.time() - start))
            finally:
                client.disconnect()
            return results

        print(f"压力测试3: 发送消息 - {CONCURRENT_USERS}并发, 每用户{REQUESTS_PER_USER}请求 (长连接)")
        start_time = time.time()
        with ThreadPoolExecutor(max_workers=CONCURRENT_USERS) as executor:
            futures = [executor.submit(message_worker, i) for i in range(CONCURRENT_USERS)]
            all_results = []
            for f in as_completed(futures):
                all_results.extend(f.result())
        total_time = time.time() - start_time

        success_count = sum(1 for s, _ in all_results if s)
        fail_count = len(all_results) - success_count
        durations = [d for _, d in all_results if d > 0]
        if durations:
            avg_dur = sum(durations) / len(durations)
            max_dur = max(durations)
            min_dur = min(durations)
        else:
            avg_dur = max_dur = min_dur = 0
        qps = len(all_results) / total_time if total_time > 0 else 0

        self.report.add_pressure_result(
            "send_message_concurrent",
            len(all_results), success_count, fail_count,
            avg_dur, max_dur, min_dur, qps
        )

        print("压力测试完成。")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="server.py 测试套件")
    parser.add_argument("--host", default=DEFAULT_HOST, help="服务器IP地址")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="服务器端口")
    parser.add_argument("--test", choices=["protocol", "unit", "stress", "all"], default="all", help="测试类型")
    args = parser.parse_args()

    # 检查服务器连通性
    try:
        sock = socket.socket()
        sock.settimeout(3)
        sock.connect((args.host, args.port))
        sock.close()
    except Exception as e:
        print(f"错误：无法连接到服务器 {args.host}:{args.port} - {e}")
        print("请确保 server.py 正在运行。")
        sys.exit(1)

    suite = TestSuite(args.host, args.port)

    if args.test == "protocol":
        suite.test_protocol()
        suite.report.generate()
    elif args.test == "unit":
        suite.test_unit()
        suite.report.generate()
    elif args.test == "stress":
        suite.test_stress()
        suite.report.generate()
    else:
        suite.run_all()


if __name__ == "__main__":
    main()