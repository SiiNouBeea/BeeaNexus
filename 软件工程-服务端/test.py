#!/usr/bin/env python3
"""
server.py pytest 测试套件
测试项目：
- 接口/协议测试：验证各路由的正确性、错误处理、协议格式
- 压力测试：并发请求下的稳定性、响应时间、成功率
- 单元测试：各路由函数的边界条件和业务逻辑

运行方式：
    pytest test_server.py -v                          # 运行所有测试
    pytest test_server.py::TestProtocol -v             # 只运行协议测试
    pytest test_server.py::TestUnit -v                 # 只运行单元测试
    pytest test_server.py::TestStress -v               # 只运行压力测试
    pytest test_server.py --host=127.0.0.1 --port=8000 # 指定服务器地址

生成报告：
    pytest test_server.py --junitxml=test_report.xml   # JUnit XML 报告
    pytest test_server.py --html=test_report.html      # HTML 报告 (需安装 pytest-html)
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
import pytest

# ==================== 配置 ====================
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
REQUEST_TIMEOUT = 10
CONNECTION_TIMEOUT = 5

# 测试账号配置
TEST_USERNAME = "test_user_" + ''.join(random.choices(string.ascii_lowercase, k=6))
TEST_PASSWORD = "TestPass123"
TEST_EMAIL = f"test_{random.randint(1000, 9999)}@test.com"
TEST_PHONE = "138" + ''.join(random.choices(string.digits, k=8))
TEST_PLAYERNAME = "TestPlayer_" + ''.join(random.choices(string.ascii_letters, k=5))


# ==================== TCP 客户端 ====================
class ServerClient:
    """TCP JSON 客户端，用于与服务端通信"""

    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT):
        self.host = host
        self.port = port
        self.sock = None
        self.seq = 0

    def connect(self):
        """建立连接"""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(CONNECTION_TIMEOUT)
        self.sock.connect((self.host, self.port))

    def disconnect(self):
        """断开连接"""
        if self.sock:
            self.sock.close()
            self.sock = None

    def _pack(self, msg: dict) -> bytes:
        """打包消息"""
        body = json.dumps(msg, ensure_ascii=False).encode('utf-8')
        return struct.pack('>I', len(body)) + body

    def _recv_exact(self, n: int) -> bytes:
        """精确接收 n 字节"""
        buf = bytearray()
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionResetError("连接已关闭")
            buf.extend(chunk)
        return bytes(buf)

    def send_request(self, req_type: str, data: dict, timeout=REQUEST_TIMEOUT) -> dict:
        """发送请求并返回响应（每次自动建立新连接）"""
        self.seq += 1
        req = {"type": req_type, "seq": self.seq}
        req.update(data)

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
        """使用现有连接发送请求（用于长连接复用）"""
        self.seq += 1
        req = {"type": req_type, "seq": self.seq}
        req.update(data)

        self.sock.settimeout(timeout)
        self.sock.sendall(self._pack(req))
        len_bs = self._recv_exact(4)
        body_len = struct.unpack('>I', len_bs)[0]
        body = self._recv_exact(body_len)
        return json.loads(body.decode('utf-8'))


# ==================== Pytest Fixtures ====================
@pytest.fixture(scope="session")
def server_config(request):
    """获取服务器配置（支持命令行参数）"""
    host = request.config.getoption("--host", default=DEFAULT_HOST)
    port = request.config.getoption("--port", default=DEFAULT_PORT)
    return {"host": host, "port": port}


@pytest.fixture(scope="session")
def check_server(server_config):
    """检查服务器是否可连接"""
    try:
        sock = socket.socket()
        sock.settimeout(3)
        sock.connect((server_config["host"], server_config["port"]))
        sock.close()
    except Exception as e:
        pytest.skip(f"无法连接到服务器 {server_config['host']}:{server_config['port']} - {e}")


@pytest.fixture(scope="module")
def test_client(server_config, check_server):
    """创建测试客户端实例"""
    client = ServerClient(server_config["host"], server_config["port"])
    yield client
    client.disconnect()


@pytest.fixture(scope="module")
def authenticated_user(test_client):
    """创建并认证测试用户，返回用户ID"""
    # 尝试注册新用户
    reg_data = {
        "username": TEST_USERNAME,
        "password": TEST_PASSWORD,
        "nickname": "TestNick",
        "email": TEST_EMAIL,
        "phone": TEST_PHONE,
        "playername": TEST_PLAYERNAME
    }

    reg_resp = test_client.send_request("register", reg_data)

    # 登录获取用户ID
    login_resp = test_client.send_request("login", {
        "username": TEST_USERNAME,
        "password": TEST_PASSWORD
    })

    if not login_resp.get("success"):
        pytest.fail(f"登录失败: {login_resp.get('message')}")

    user_id = login_resp["user"]["UserID"]
    return {
        "user_id": user_id,
        "username": TEST_USERNAME,
        "password": TEST_PASSWORD
    }


# ==================== 协议测试 ====================
class TestProtocol:
    """接口/协议测试类"""

    def test_register_new_user(self, test_client):
        """测试：注册新用户"""
        username = f"new_user_{random.randint(1000, 9999)}"
        resp = test_client.send_request("register", {
            "username": username,
            "password": "Pass123",
            "nickname": "NewUser",
            "email": f"{username}@test.com",
            "phone": "13800138000",
            "playername": f"Player_{username}"
        })
        assert resp.get("success") is True, f"注册失败: {resp.get('message')}"

    def test_register_duplicate_username(self, test_client, authenticated_user):
        """测试：重复用户名注册应失败"""
        resp = test_client.send_request("register", {
            "username": authenticated_user["username"],
            "password": TEST_PASSWORD,
            "nickname": "Duplicate",
            "email": "dup@test.com",
            "phone": "13800138001",
            "playername": "DupPlayer"
        })
        assert resp.get("success") is False, "重复注册应该失败"

    def test_login_correct(self, test_client, authenticated_user):
        """测试：使用正确密码登录"""
        resp = test_client.send_request("login", {
            "username": authenticated_user["username"],
            "password": authenticated_user["password"]
        })
        assert resp.get("success") is True, f"登录失败: {resp.get('message')}"
        assert "user" in resp, "响应中应包含用户信息"

    def test_login_wrong_password(self, test_client, authenticated_user):
        """测试：使用错误密码登录应失败"""
        resp = test_client.send_request("login", {
            "username": authenticated_user["username"],
            "password": "wrong_password"
        })
        assert resp.get("success") is False, "错误密码应该登录失败"

    def test_get_profile(self, test_client, authenticated_user):
        """测试：获取个人信息"""
        resp = test_client.send_request("profile", {
            "user_id": authenticated_user["user_id"]
        })
        assert resp.get("success") is True, f"获取资料失败: {resp.get('message')}"
        assert "user" in resp, "响应中应包含用户信息"

    def test_sign_in(self, test_client, authenticated_user):
        """测试：签到功能"""
        resp = test_client.send_request("sign", {
            "user_id": authenticated_user["user_id"]
        })
        # 可能成功或已签到
        assert "success" in resp, "签到响应格式不正确"

    def test_leaderboard(self, test_client):
        """测试：获取排行榜"""
        resp = test_client.send_request("leaderboard", {})
        assert resp.get("success") is True, f"获取排行榜失败: {resp.get('message')}"
        assert "coin" in resp or "star" in resp, "响应中应包含排行榜数据"

    def test_get_contacts(self, test_client, authenticated_user):
        """测试：获取联系人列表"""
        resp = test_client.send_request("get_contacts", {
            "user_id": authenticated_user["user_id"]
        })
        assert resp.get("success") is True, f"获取联系人失败: {resp.get('message')}"
        assert "contacts" in resp, "响应中应包含联系人列表"

    def test_send_message(self, test_client, authenticated_user):
        """测试：发送消息"""
        resp = test_client.send_request("send_message", {
            "sender_id": authenticated_user["user_id"],
            "receiver_id": authenticated_user["user_id"],
            "content": "测试消息内容"
        })
        assert resp.get("success") is True, f"发送消息失败: {resp.get('message')}"

    def test_get_unread_messages(self, test_client, authenticated_user):
        """测试：获取未读消息"""
        resp = test_client.send_request("get_unread_messages", {
            "user_id": authenticated_user["user_id"]
        })
        assert resp.get("success") is True, f"获取未读消息失败: {resp.get('message')}"

    def test_update_profile(self, test_client, authenticated_user):
        """测试：更新个人资料"""
        resp = test_client.send_request("update_profile", {
            "user_id": authenticated_user["user_id"],
            "nickname": "UpdatedNick",
            "email": TEST_EMAIL,
            "phone": TEST_PHONE,
            "first_name": "Test",
            "last_name": "User",
            "gender": "男",
            "birthday": "2000-01-01",
            "bio": "Test bio"
        })
        assert resp.get("success") is True, f"更新资料失败: {resp.get('message')}"

    def test_invalid_request_type(self, test_client):
        """测试：无效的请求类型"""
        resp = test_client.send_request("nonexistent_type", {})
        assert resp.get("success") is False, "无效请求类型应该返回失败"

    def test_missing_required_fields(self, test_client):
        """测试：缺少必填字段"""
        resp = test_client.send_request("login", {})
        assert resp.get("success") is False, "缺少必填字段应该返回失败"


# ==================== 单元测试 ====================
class TestUnit:
    """单元测试类 - 测试边界条件和业务逻辑"""

    def test_register_invalid_email(self, test_client):
        """测试：注册时邮箱格式验证"""
        resp = test_client.send_request("register", {
            "username": f"invalid_email_{random.randint(1000, 9999)}",
            "password": "pass123",
            "nickname": "Test",
            "email": "not_an_email",
            "phone": "13800138000",
            "playername": "player"
        })
        assert resp.get("success") is False, "无效邮箱应该注册失败"
        assert "邮箱" in resp.get("message", ""), "错误消息应提示邮箱问题"

    def test_register_invalid_phone(self, test_client):
        """测试：注册时手机号格式验证"""
        resp = test_client.send_request("register", {
            "username": f"invalid_phone_{random.randint(1000, 9999)}",
            "password": "pass123",
            "nickname": "Test",
            "email": "test@test.com",
            "phone": "123",
            "playername": "player"
        })
        assert resp.get("success") is False, "无效手机号应该注册失败"
        assert "手机" in resp.get("message", ""), "错误消息应提示手机号问题"

    def test_send_long_message(self, test_client, authenticated_user):
        """测试：发送超长消息应被截断但仍成功"""
        long_content = "A" * 300
        resp = test_client.send_request("send_message", {
            "sender_id": authenticated_user["user_id"],
            "receiver_id": authenticated_user["user_id"],
            "content": long_content
        })
        assert resp.get("success") is True, "超长消息应该被截断但仍成功发送"

    def test_give_gift_exceed_limit(self, test_client, authenticated_user):
        """测试：赠与金币超过每日上限"""
        uid = authenticated_user["user_id"]

        # 先赠送到接近上限
        for _ in range(6):
            test_client.send_request("give_gift", {
                "sender_id": uid,
                "receiver_id": uid,
                "gift_type": "coin"
            })

        # 再次赠与应该失败
        resp = test_client.send_request("give_gift", {
            "sender_id": uid,
            "receiver_id": uid,
            "gift_type": "coin"
        })
        assert resp.get("success") is False, "超过赠与上限应该失败"
        assert "上限" in resp.get("message", ""), "错误消息应提示上限问题"

    def test_get_nonexistent_user(self, test_client, authenticated_user):
        """测试：获取不存在的用户资料"""
        resp = test_client.send_request("get_user_profile", {
            "user_id": authenticated_user["user_id"],
            "target_id": 99999
        })
        assert resp.get("success") is False, "不存在的用户应该返回失败"

    def test_insufficient_permission(self, test_client, authenticated_user):
        """测试：普通用户不能修改权限"""
        resp = test_client.send_request("update_role", {
            "user_id": authenticated_user["user_id"],
            "role_id": 1
        })
        assert resp.get("success") is False, "普通用户不应该能修改权限"

    def test_duplicate_whitelist_apply(self, test_client, authenticated_user):
        """测试：白名单重复申请"""
        uid = authenticated_user["user_id"]

        # 第一次申请
        test_client.send_request("whitelist_apply", {
            "user_id": uid,
            "playername": TEST_PLAYERNAME,
            "genuine": 1,
            "reason": "test reason"
        })

        # 第二次申请应该失败
        resp = test_client.send_request("whitelist_apply", {
            "user_id": uid,
            "playername": TEST_PLAYERNAME,
            "genuine": 1,
            "reason": "test reason"
        })
        assert resp.get("success") is False, "重复申请应该失败"
        assert "未审核" in resp.get("message", ""), "错误消息应提示未审核状态"

    def test_add_self_as_contact(self, test_client, authenticated_user):
        """测试：不能添加自己为联系人"""
        resp = test_client.send_request("add_contact", {
            "user_id": authenticated_user["user_id"],
            "contact_id": authenticated_user["user_id"]
        })
        assert resp.get("success") is False, "不能添加自己为联系人"


# ==================== 压力测试 ====================
class TestStress:
    """压力测试类 - 并发请求测试"""

    @pytest.fixture(autouse=True)
    def setup_stress_test(self, authenticated_user):
        """压力测试前置条件检查"""
        if not authenticated_user.get("user_id"):
            pytest.skip("无测试用户，跳过压力测试")
        self.user_info = authenticated_user

    def _run_concurrent_test(self, worker_func, concurrent_users=10, requests_per_user=50, test_name="stress_test"):
        """通用并发测试执行器"""
        total_requests = concurrent_users * requests_per_user

        start_time = time.time()
        with ThreadPoolExecutor(max_workers=concurrent_users) as executor:
            futures = [executor.submit(worker_func, i, requests_per_user) for i in range(concurrent_users)]
            all_results = []
            for f in as_completed(futures):
                try:
                    all_results.extend(f.result())
                except Exception as e:
                    pytest.fail(f"工作线程异常: {e}")

        total_time = time.time() - start_time

        # 统计结果
        success_count = sum(1 for s, _ in all_results if s)
        fail_count = len(all_results) - success_count
        durations = [d for _, d in all_results if d > 0]

        avg_dur = sum(durations) / len(durations) if durations else 0
        max_dur = max(durations) if durations else 0
        min_dur = min(durations) if durations else 0
        qps = len(all_results) / total_time if total_time > 0 else 0

        # 记录性能指标（可通过 pytest-metadata 插件收集）
        print(f"\n[{test_name}] 性能指标:")
        print(f"  总请求: {len(all_results)}, 成功: {success_count}, 失败: {fail_count}")
        print(f"  QPS: {qps:.2f}")
        print(f"  响应时间(秒) - 平均: {avg_dur:.3f}, 最小: {min_dur:.3f}, 最大: {max_dur:.3f}")

        # 断言：成功率应该大于 95%
        success_rate = success_count / len(all_results) * 100 if all_results else 0
        assert success_rate >= 95, f"成功率过低: {success_rate:.2f}% (期望 >= 95%)"

        # 断言：平均响应时间应该小于 2 秒
        assert avg_dur < 2.0, f"平均响应时间过长: {avg_dur:.3f}s (期望 < 2.0s)"

        return {
            "total": len(all_results),
            "success": success_count,
            "failed": fail_count,
            "avg_time": avg_dur,
            "max_time": max_dur,
            "min_time": min_dur,
            "qps": qps
        }

    def test_login_concurrent(self, server_config):
        """压力测试：登录接口并发性能"""
        concurrent_users = 10
        requests_per_user = 50

        def login_worker(worker_id, req_count):
            client = ServerClient(server_config["host"], server_config["port"])
            results = []
            for i in range(req_count):
                start = time.time()
                try:
                    resp = client.send_request("login", {
                        "username": TEST_USERNAME,
                        "password": TEST_PASSWORD
                    })
                    dur = time.time() - start
                    results.append((resp.get("success", False), dur))
                except Exception:
                    results.append((False, time.time() - start))
                    client.disconnect()
            return results

        self._run_concurrent_test(
            login_worker,
            concurrent_users=concurrent_users,
            requests_per_user=requests_per_user,
            test_name="login_concurrent"
        )

    def test_profile_concurrent(self, server_config, authenticated_user):
        """压力测试：获取个人信息接口并发性能（长连接）"""
        concurrent_users = 10
        requests_per_user = 50
        uid = authenticated_user["user_id"]

        def profile_worker(worker_id, req_count):
            client = ServerClient(server_config["host"], server_config["port"])
            results = []
            try:
                # 先登录
                login_resp = client.send_request("login", {
                    "username": TEST_USERNAME,
                    "password": TEST_PASSWORD
                })
                if not login_resp.get("success"):
                    return [(False, 0)] * req_count

                # 建立长连接
                client.connect()
                for i in range(req_count):
                    start = time.time()
                    try:
                        resp = client.send_request_raw("profile", {"user_id": uid})
                        dur = time.time() - start
                        results.append((resp.get("success", False), dur))
                    except Exception:
                        # 重试一次
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

        self._run_concurrent_test(
            profile_worker,
            concurrent_users=concurrent_users,
            requests_per_user=requests_per_user,
            test_name="profile_concurrent"
        )

    def test_send_message_concurrent(self, server_config, authenticated_user):
        """压力测试：发送消息接口并发性能（长连接）"""
        concurrent_users = 10
        requests_per_user = 50
        uid = authenticated_user["user_id"]

        def message_worker(worker_id, req_count):
            client = ServerClient(server_config["host"], server_config["port"])
            results = []
            try:
                # 先登录
                login_resp = client.send_request("login", {
                    "username": TEST_USERNAME,
                    "password": TEST_PASSWORD
                })
                if not login_resp.get("success"):
                    return [(False, 0)] * req_count

                # 建立长连接
                client.connect()
                for i in range(req_count):
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
                        # 重试一次
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

        self._run_concurrent_test(
            message_worker,
            concurrent_users=concurrent_users,
            requests_per_user=requests_per_user,
            test_name="send_message_concurrent"
        )


# ==================== Pytest 命令行参数 ====================
def pytest_addoption(parser):
    """添加自定义命令行参数"""
    parser.addoption(
        "--host",
        action="store",
        default=DEFAULT_HOST,
        help="服务器IP地址 (默认: 127.0.0.1)"
    )
    parser.addoption(
        "--port",
        action="store",
        type=int,
        default=DEFAULT_PORT,
        help="服务器端口 (默认: 8000)"
    )


# ==================== 主入口（兼容直接运行） ====================
if __name__ == "__main__":
    # 构建 pytest 命令
    pytest_args = [__file__, "-v", "--tb=short"]

    # 添加命令行参数
    if len(sys.argv) > 1:
        pytest_args.extend(sys.argv[1:])

    # 执行 pytest
    exit_code = pytest.main(pytest_args)
    sys.exit(exit_code)
