#!/usr/bin/env python3
import sys, json, socket, threading, traceback, time
from PyQt5.QtWidgets import *
from PyQt5.QtGui import *
from PyQt5.QtCore import *
import struct
import datetime
import socket as socket_module  # 添加socket模块用于获取本地IP
import requests  # 添加requests模块用于获取公网IP
from functools import partial


# ========================= 网络客户端 =========================
class DesktopClient(QObject):
    real_time_message = pyqtSignal(dict)  # 供界面连接
    resp_sig = pyqtSignal(dict)  # 添加resp_sig信号定义

    def __init__(self, host="frp-off.com", port=52784):  # frp-off.com:52784
        super().__init__()
        self.real_time_message.connect(self._on_real_time, Qt.QueuedConnection)
        self.host, self.port = host, port
        self._sock = None
        self._lock = threading.Lock()
        self._seq = 0
        self._pendings = {}
        self.client_ip = self._get_public_ip()
        self._connect()

    # -------------- 基础网络 --------------
    def _get_public_ip(self):
        try:
            return requests.get("http://ip-api.com/json/", timeout=3).json().get("query", "127.0.0.1")
        except:
            return "127.0.0.1"

    def _connect(self):
        try:
            self._sock = socket.create_connection((self.host, self.port))
            threading.Thread(target=self._recv_loop, daemon=True).start()
        except Exception as e:
            QMessageBox.critical(None, "网络错误", f"无法连接服务器：{e}")
            sys.exit(1)

    def _recv_loop(self):
        while True:
            try:
                len_bs = self._recv_exact(self._sock, 4)
                body_len = struct.unpack('>I', len_bs)[0]
                body = self._recv_exact(self._sock, body_len)
                js = json.loads(body.decode('utf-8'))
                self._dispatch(js)
            except Exception:
                # 连接断开时通知主窗口
                if hasattr(self, '_main') and self._main:
                    QMetaObject.invokeMethod(self._main, "_on_connection_lost", Qt.QueuedConnection)
                break

    def _dispatch(self, resp: dict):
        # 推送类消息（无 seq）
        if resp.get("type") == "real_time_message":
            self.real_time_message.emit(resp)
            return
        # 正常响应
        seq = resp.get("seq")
        cb = self._pendings.pop(seq, None)
        if cb:
            # 修改为使用resp_sig信号发射响应
            # 直接调用回调函数
            QMetaObject.invokeMethod(self, "_exec_cb", Qt.QueuedConnection,
                                     Q_ARG(object, cb), Q_ARG(object, resp))
            print(f"[C] 客户端收到响应，类型: {resp['type']}")
        else:
            pass  # 原本这里的逻辑是重复的，现在移除

    @pyqtSlot(object, object)
    def _exec_cb(self, cb, resp):
        cb(resp)

    def send(self, req: dict, callback=None):
        with self._lock:
            self._seq += 1
            seq = self._seq
            req["seq"] = seq
            req["client_ip"] = self.client_ip
            if callback:
                self._pendings[seq] = callback
            try:
                self._sock.sendall(self._pack(req))
            except Exception:
                # 发送失败时通知主窗口
                if hasattr(self, '_main') and self._main:
                    QMetaObject.invokeMethod(self._main, "_on_connection_lost", Qt.QueuedConnection)

    @staticmethod
    def _pack(msg: dict) -> bytes:
        body = json.dumps(msg, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        return struct.pack('>I', len(body)) + body

    @staticmethod
    def _recv_exact(sock, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionResetError
            buf.extend(chunk)
        return bytes(buf)

    # -------------- 实时消息处理 --------------
    def _on_real_time(self, resp: dict):
        """主窗口已连接此信号，无需再做分发"""
        self.resp_sig.emit(resp)


# ========================= 主界面 =========================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("用户管理系统 — 桌面版")
        self.resize(1000, 700)
        self.client = DesktopClient()
        self.client._main = self  # 将主窗口引用传递给client用于获取用户信息
        self.user = None
        self.unread_count = 0  # 添加未读消息计数
        self.server_status_timer = QTimer()  # 添加服务器状态定时器
        self.server_status_timer.timeout.connect(self.refresh_server_status)
        self.online_users = set()  # 在线用户集合
        self._init_ui()
        self._bind_signal()

    # -------------------- UI 骨架 --------------------
    def _init_ui(self):
        self.nav = QListWidget()
        self.nav.setFixedWidth(160)
        self.nav.addItems(["主页", "签到/排行", "个人资料"])
        self.nav.setCurrentRow(0)
        self.nav.itemClicked.connect(self._switch_tab)

        self.stack = QStackedWidget()
        self.stack.addWidget(HomePage(self))  # 0
        self.stack.addWidget(DailyPage(self))  # 1
        self.stack.addWidget(ProfilePage(self))  # 2
        self.stack.addWidget(WhitelistPage(self))  # 3
        #self.stack.addWidget(QQPage(self))  # 4
        self.stack.addWidget(MessagePage(self))  # 5 添加传声筒页面
        self.stack.addWidget(AdminPage(self))  # 6 管理员面板移到索引6

        # 只有在需要时才添加服务器控制台页面
        # self.stack.addWidget(ServerConsolePage(self))  # 7 添加服务器控制台页面

        self.status = QLabel("未登录")
        self.statusBar().addWidget(self.status)

        central = QWidget()
        lay = QHBoxLayout(central)
        lay.addWidget(self.nav)
        lay.addWidget(self.stack)
        self.setCentralWidget(central)

        self.menubar = self.menuBar()
        self.userMenu = self.menubar.addMenu("用户(U)")
        self.loginAction = self.userMenu.addAction("登录(L)")
        self.registerAction = self.userMenu.addAction("注册(R)")
        self.userMenu.addSeparator()
        self.logoutAction = self.userMenu.addAction("登出")
        self.quitAction = self.userMenu.addAction("退出(Q)")

        # 初始时隐藏登出选项
        self.logoutAction.setVisible(False)

        self.loginAction.triggered.connect(self._login)
        self.registerAction.triggered.connect(self._register)
        self.logoutAction.triggered.connect(self._logout)
        self.quitAction.triggered.connect(qApp.quit)

        print(f"[C] MainWindow created in thread {int(QThread.currentThreadId())}")

    # 添加更新未读消息数的方法
    def _update_unread_count(self, delta):
        """更新未读消息数"""
        self.unread_count += delta
        # 确保未读计数不为负数
        self.unread_count = max(0, self.unread_count)

        # 更新导航栏中传声筒项的显示
        for i in range(self.nav.count()):
            item = self.nav.item(i)
            if item.text().startswith("传声筒"):
                if self.unread_count > 0:
                    item.setText(f"传声筒 ({self.unread_count})")
                else:
                    item.setText("传声筒")
                break

    # 添加刷新服务器状态的方法（仅在管理员登录后使用）
    def refresh_server_status(self):
        """刷新服务器状态"""
        if self.user:
            self.client.send({
                "type": "get_server_status"
            }, lambda resp: self._on_server_status(resp))

    # -------------------- 信号绑定 --------------------
    def _bind_signal(self):
        self.client.resp_sig.connect(self._on_resp)

    # -------------------- 槽：保底兼容 --------------------
    def _on_resp(self, resp: dict):
        t = resp.get("type")
        if t == "login":
            if resp.get("success"):
                self.user = resp["user"]
                self.status.setText(f"已登录：{self.user['Username']}")

                # 更新在线用户列表
                if "online_users" in resp:
                    self.online_users = set(resp["online_users"])
                    # 更新主窗口中的在线用户列表
                    if hasattr(self, 'stack'):
                        for i in range(self.stack.count()):
                            page = self.stack.widget(i)
                            if isinstance(page, MessagePage):
                                page.online_users = self.online_users
                                if hasattr(page, '_refresh_contact_list'):
                                    page._refresh_contact_list()
                                break

                self._update_navbar_visibility()  # 添加：更新导航栏可见性
                # 登录成功时上报在线状态
                self.client.send({"type": "user_online", "user_id": self.user["UserID"]},
                                 callback=lambda r: None)

                # 登录成功后查询未读消息
                self._check_unread_messages()

                # 登录成功后立即获取联系人列表
                for i in range(self.stack.count()):
                    page = self.stack.widget(i)
                    if isinstance(page, MessagePage):
                        page._refresh_contacts()
                        break

                # 如果是管理员，显示管理员面板和服务器控制台
                if self.user.get('RoleID') == 1:
                    self._show_admin_interface()

                # 切换到主页
                self.nav.setCurrentRow(0)
                self._switch_tab()

                QMessageBox.information(self, "登录成功", f"欢迎回来，{self.user['Nickname']}")
            else:
                QMessageBox.warning(self, "登录失败", resp.get("message", "未知错误"))

        elif t == "register":
            if resp.get("success"):
                QMessageBox.information(self, "注册成功", "请登录")
            else:
                QMessageBox.warning(self, "注册失败", resp.get("message", "未知错误"))

        elif t == "user_online" or t == "get_contacts":
            if "online_users" in resp:
                self.online_users = set(resp["online_users"])
                # 更新所有消息页面的在线用户列表
                if hasattr(self, 'stack'):
                    for i in range(self.stack.count()):
                        page = self.stack.widget(i)
                        if isinstance(page, MessagePage):
                            page.online_users = self.online_users
                            if hasattr(page, '_refresh_contact_list'):
                                page._refresh_contact_list()

        # 添加对实时消息的处理
        elif t == "real_time_message":
            # 处理实时消息，即使不在传声筒页面也要处理未读消息
            self._handle_real_time_message(resp)
        # 添加对未读消息查询结果的处理
        elif t == "get_unread_messages":
            if resp.get("success"):
                unread_count = resp.get("unread_count", 0)
                if unread_count > 0:
                    print(f"[C] 用户{self.user['Username']} 有 {unread_count} 条未读消息")
                    self._update_unread_count(unread_count)
                    # 更新消息页面的未读计数
                    for i in range(self.stack.count()):
                        page = self.stack.widget(i)
                        if isinstance(page, MessagePage):
                            page.unread_counts = resp.get("unread_details", {})
                            if hasattr(page, '_refresh_contact_list'):
                                page._refresh_contact_list()
                            break

    def _handle_real_time_message(self, resp):
        """处理实时消息"""
        if resp.get("type") == "real_time_message":
            message = resp.get("message", {})
            sender_id = message.get("sender_id")

            # 更新未读消息计数（无论在哪个页面）
            sender_id_str = str(sender_id)
            # 查找消息页面并更新未读计数
            for i in range(self.stack.count()):
                page = self.stack.widget(i)
                if isinstance(page, MessagePage):
                    # 更新未读消息计数
                    page.unread_counts[sender_id_str] = page.unread_counts.get(sender_id_str, 0) + 1
                    self._update_unread_count(1)  # 更新总未读数
                    if hasattr(page, '_refresh_contact_list'):
                        page._refresh_contact_list()  # 刷新联系人列表显示

                    # 如果当前在消息页面，直接显示消息
                    if self.stack.currentIndex() == 5:  # 传声筒页面索引为5
                        # 如果正在与发送方聊天，直接显示消息
                        if page.current_contact and page.current_contact["UserID"] == sender_id:
                            if hasattr(page, '_display_new_message'):
                                page._display_new_message(message)
                            # 当用户正在查看聊天时，标记消息为已读
                            if self.user:
                                self.client.send({
                                    "type": "mark_messages_as_read",
                                    "user_id": self.user["UserID"],
                                    "contact_id": sender_id
                                }, callback=lambda r: None)
                    break

    def _check_unread_messages(self):
        """查询用户未读消息"""
        if self.user:
            # 发送请求获取未读消息
            self.client.send({
                "type": "get_unread_messages",
                "user_id": self.user["UserID"]
            }, callback=self._on_resp)

    def _on_server_status(self, resp):
        """处理服务器状态响应"""
        if resp.get("success"):
            # 更新主页面的服务器状态显示
            home_page = self.stack.widget(0)
            if hasattr(home_page, 'update_server_status'):
                home_page.update_server_status(resp)

    def _update_navbar_visibility(self):
        """根据用户权限更新导航栏显示"""
        if self.user and self.user.get('RoleID') == 1:  # 管理员
            # 确保导航栏包含所有项目
            current_items = [self.nav.item(i).text() for i in range(self.nav.count())]
            if "白名单申请" not in current_items:
                self.nav.addItem("白名单申请")
            if "传声筒" not in current_items:
                self.nav.addItem("传声筒")
            if "管理员面板" not in current_items:
                self.nav.addItem("管理员面板")
            if "服务器控制台" not in current_items:
                self.nav.addItem("服务器控制台")
        elif self.user:  # 普通用户
            # 只显示基本功能
            current_items = [self.nav.item(i).text() for i in range(self.nav.count())]
            # 清除管理员项目
            items_to_remove = []
            for i in range(self.nav.count()):
                item_text = self.nav.item(i).text()
                if item_text in ["管理员面板", "服务器控制台"]:
                    items_to_remove.append(self.nav.item(i))

            for item in items_to_remove:
                row = self.nav.row(item)
                self.nav.takeItem(row)

            # 添加用户功能
            if "白名单申请" not in current_items:
                self.nav.addItem("白名单申请")
            if "传声筒" not in current_items:
                self.nav.addItem("传声筒")
        else:  # 未登录
            # 重置导航栏
            self.nav.clear()
            self.nav.addItems(["主页", "签到/排行", "个人资料"])

    def _show_admin_interface(self):
        """显示管理员界面"""
        # 确保管理员可以看到所有功能
        self._update_navbar_visibility()
        # 添加服务器控制台页面
        if self.stack.count() < 8:  # 确保没有重复添加
            self.stack.addWidget(ServerConsolePage(self))
        # 启动服务器状态定时器
        self.server_status_timer.start(30000)  # 每30秒刷新一次

    def _switch_tab(self):
        idx = self.nav.currentRow()
        # 修改索引检查，管理员面板现在在索引6
        if idx == 6 and (not self.user or not self.user.get("RoleID") or self.user.get("RoleID") != 1):
            QMessageBox.warning(self, "权限不足", "仅管理员可见")
            # 重置选择到当前页面而不是主页
            current_idx = self.stack.currentIndex()
            self.nav.setCurrentRow(current_idx)
            return
        # 添加页面切换的日志信息
        current_time = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        user_info = "未登录" if not self.user else self.user['Username']
        # 更新页面名称列表，添加传声筒选项
        page_names = ["主页", "签到/排行", "个人资料", "白名单申请", "QQ 绑定", "传声筒", "管理员控制台",
                      "服务器控制台"]
        page_name = page_names[idx] if idx < len(page_names) else f"页面{idx}"
        print(f"[C] {current_time} <{user_info}> 切换到页面: {page_name}")

        # 更新导航栏当前项
        self.nav.setCurrentRow(idx)

        # 检查索引是否在范围内
        if idx < self.stack.count():
            self.stack.setCurrentIndex(idx)
        else:
            print(f"[C] 警告: 索引 {idx} 超出范围，最大索引为 {self.stack.count() - 1}")
            self.stack.setCurrentIndex(0)

        # 添加：切换页面时刷新内容
        current_page = self.stack.currentWidget()
        if hasattr(current_page, '_refresh'):
            current_page._refresh()

        # 如果切换到传声筒页面，更新未读消息显示
        if idx == 5:  # 传声筒页面
            self._update_unread_count(0)  # 确保未读计数正确显示

    # -------------------- 登录/注册 --------------------
    def _login(self):
        LoginDialog(self).exec_()

    def _register(self):
        RegisterDialog(self).exec_()

    def _logout(self):
        if self.user:
            self.client.send({"type": "user_offline", "user_id": self.user['UserID']})
        self.user = None
        self.status.setText("未登录")
        self.logoutAction.setVisible(False)
        self.loginAction.setVisible(True)
        self.registerAction.setVisible(True)

        # 停止服务器状态定时器
        self.server_status_timer.stop()

        # 重置导航栏
        self.nav.clear()
        self.nav.addItems(["主页", "签到/排行", "个人资料"])
        self.nav.setCurrentRow(0)
        self._switch_tab()

        QMessageBox.information(self, "提示", "您已成功登出！")

    def closeEvent(self, e):
        if self.user:
            self.client.send({"type": "user_offline", "user_id": self.user['UserID']})
        e.accept()


# ========================= 通用弹窗 =========================
class LoginDialog(QDialog):
    def __init__(self, parent: MainWindow):
        super().__init__(parent, Qt.WindowCloseButtonHint)
        self.setWindowTitle("登录")
        self.main = parent
        self.client = parent.client
        self._init_ui()

    def _init_ui(self):
        form = QFormLayout(self)
        self.userEdit = QLineEdit()
        self.pwdEdit = QLineEdit()
        self.pwdEdit.setEchoMode(QLineEdit.Password)
        form.addRow("用户名：", self.userEdit)
        form.addRow("密  码：", self.pwdEdit)
        box = QHBoxLayout()
        self.okBtn = QPushButton("登录")
        self.okBtn.clicked.connect(self._do_login)
        box.addWidget(self.okBtn)
        box.addWidget(QPushButton("取消", clicked=self.reject))
        form.addRow(box)

    def _do_login(self):
        u, p = self.userEdit.text(), self.pwdEdit.text()
        if not u or not p:
            QMessageBox.warning(self, "提示", "请输入完整")
            return
        self.client.send({"type": "login", "username": u, "password": p},
                         callback=self._on_login_resp)

    def _on_login_resp(self, resp):
        if resp.get("success"):
            self.main.user = resp["user"]
            self.main.status.setText(f"已登录：{self.main.user['Username']}")
            self.main._update_navbar_visibility()  # 添加：更新导航栏可见性
            # 登录成功时上报在线状态
            self.main.client.send({"type": "user_online", "user_id": self.main.user["UserID"]},
                                  callback=lambda r: None)
            QMessageBox.information(self, "提示", "登录成功！")
            self.accept()
        else:
            QMessageBox.warning(self, "失败", resp.get("message"))


class RegisterDialog(QDialog):
    def __init__(self, parent: MainWindow):
        super().__init__(parent, Qt.WindowCloseButtonHint)
        self.setWindowTitle("注册")
        self.client = parent.client
        self.main = parent  # 添加对主窗口的引用
        self._init_ui()

    def _init_ui(self):
        form = QFormLayout(self)
        self.userEdit = QLineEdit()
        self.pwdEdit = QLineEdit()
        self.pwdEdit.setEchoMode(QLineEdit.Password)
        self.nickEdit = QLineEdit()
        self.emailEdit = QLineEdit()
        self.phoneEdit = QLineEdit()
        self.pnEdit = QLineEdit()
        form.addRow("用户名：", self.userEdit)
        form.addRow("密  码：", self.pwdEdit)
        form.addRow("昵  称：", self.nickEdit)
        form.addRow("邮  箱：", self.emailEdit)
        form.addRow("手机号：", self.phoneEdit)
        form.addRow("游戏名：", self.pnEdit)
        box = QHBoxLayout()
        self.okBtn = QPushButton("注册")
        self.okBtn.clicked.connect(self._do_reg)
        box.addWidget(self.okBtn)
        box.addWidget(QPushButton("取消", clicked=self.reject))
        form.addRow(box)

    def _do_reg(self):
        req = {k: w.text() for k, w in [
            ("username", self.userEdit), ("password", self.pwdEdit),
            ("nickname", self.nickEdit), ("email", self.emailEdit),
            ("phone", self.phoneEdit), ("playername", self.pnEdit)]}
        if not all(req.values()):
            QMessageBox.warning(self, "提示", "请填写完整")
            return
        req["type"] = "register"
        # 修改回调函数，在注册成功后自动打开登录页面
        self.client.send(req, callback=self._on_reg_resp)

    def _on_reg_resp(self, resp):
        msg = "注册成功！" if resp.get("success") else resp.get("message")
        QMessageBox.information(self, "提示", msg)
        if resp.get("success"):
            # 注册成功后自动关闭注册对话框并打开登录对话框
            self.accept()
            # 自动打开登录对话框
            login_dlg = LoginDialog(self.main)
            login_dlg.exec_()


# ========================= 子页面基类 =========================
class BasePage(QWidget):
    def __init__(self, parent: MainWindow):
        super().__init__()
        self.main = parent
        self.client = parent.client


# ========================= 主页 =========================
class HomePage(BasePage):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self._init_ui()

    def _init_ui(self):
        lay = QVBoxLayout(self)

        # 添加服务器状态显示区域
        self.server_status_group = QGroupBox("服务器状态")
        server_layout = QVBoxLayout()

        self.server_status_label = QLabel("正在检查服务器状态...")
        self.online_count_label = QLabel("在线人数: 0")
        self.online_players_label = QLabel("在线玩家: 无")

        # 添加刷新按钮
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self.refresh_server_status)

        server_layout.addWidget(self.server_status_label)
        server_layout.addWidget(self.online_count_label)
        server_layout.addWidget(self.online_players_label)
        server_layout.addWidget(refresh_btn)

        self.server_status_group.setLayout(server_layout)
        lay.addWidget(self.server_status_group)


    def update_server_status(self, status_data):
        """更新服务器状态显示"""
        mc_online = status_data.get("mc_server_online", False)
        online_players = status_data.get("online_players", [])

        if mc_online:
            self.server_status_label.setText("✅ Minecraft服务器在线")
            self.server_status_label.setStyleSheet("color: green; font-weight: bold;")
        else:
            self.server_status_label.setText("❌ Minecraft服务器离线")
            self.server_status_label.setStyleSheet("color: red; font-weight: bold;")

        self.online_count_label.setText(f"在线人数: {len(online_players)}")

        if online_players:
            players_text = "在线玩家: " + ", ".join(online_players)
        else:
            players_text = "在线玩家: 无"
        self.online_players_label.setText(players_text)

    def refresh_server_status(self):
        """刷新服务器状态"""
        if self.main.user:
            self.main.client.send({
                "type": "get_server_status"
            }, lambda resp: self.update_server_status(resp))




# ========================= 签到/排行页面 =========================
class DailyPage(BasePage):
    def __init__(self, parent):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        signBtn = QPushButton("今日签到")
        signBtn.clicked.connect(self._sign)
        lay.addWidget(signBtn)

        self.coinTable = QTableWidget(0, 3)
        self.coinTable.setHorizontalHeaderLabels(["UID", "昵称", "硬币"])
        self.starTable = QTableWidget(0, 3)
        self.starTable.setHorizontalHeaderLabels(["UID", "昵称", "星星"])
        lay.addWidget(QLabel("硬币排行"))
        lay.addWidget(self.coinTable)
        lay.addWidget(QLabel("星星排行"))
        lay.addWidget(self.starTable)

        self.client.send({"type": "leaderboard"}, callback=self._on_leader)

    # 添加刷新方法
    def _refresh(self):
        self.client.send({"type": "leaderboard"}, callback=self._on_leader)

    def _sign(self):
        if not self.main.user:
            QMessageBox.warning(self, "提示", "请先登录")
            return
        self.client.send({"type": "sign", "user_id": self.main.user["UserID"]},
                         callback=self._on_sign)

    def _on_leader(self, resp):
        if resp.get("success"):
            self._fill_table(self.coinTable, resp["coin"], "Coins")
            self._fill_table(self.starTable, resp["star"], "Stars")

    def _on_sign(self, resp):
        msg = "签到成功！"
        if resp.get("success"):
            rw = resp.get("reward", {})
            msg = f"签到成功！获得硬币 +{rw.get('coin', 0)}"
            if rw.get("star"):
                msg += f"  星星 +{rw['star']}"
            # 签到成功后刷新排行榜
            self._refresh()
        else:
            msg = resp.get("message")
        QMessageBox.information(self, "签到结果", msg)

    def _fill_table(self, table: QTableWidget, data, key):
        table.setRowCount(len(data))
        for i, row in enumerate(data):
            table.setItem(i, 0, QTableWidgetItem(str(row["UserID"])))
            table.setItem(i, 1, QTableWidgetItem(row["Nickname"]))
            table.setItem(i, 2, QTableWidgetItem(str(row[key])))


# ========================= 个人资料页面 =========================
class ProfilePage(BasePage):
    def __init__(self, parent):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        lay = QVBoxLayout(self)

        # 创建一个滚动区域以容纳所有内容
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)

        # 标题
        title_label = QLabel("个人资料")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("font-size: 28px; font-weight: bold; margin: 10px;")  # 减少上下边距，增大字体
        scroll_layout.addWidget(title_label)

        # 用户信息卡片
        self.info_card = QFrame()
        self.info_card.setFrameStyle(QFrame.StyledPanel)
        self.info_card.setStyleSheet("""
            QFrame {
                background-color: #f9f9f9;
                border: 1px solid #ccc;
                border-radius: 8px;
                padding: 15px;
                margin: 10px;
                box-shadow: 0 4px 8px rgba(0, 0, 0, 0.1);
            }
        """)
        info_layout = QVBoxLayout(self.info_card)

        # 用户基本信息
        self.infoLab = QLabel("请先登录")
        self.infoLab.setStyleSheet("font-size: 18px; padding: 10px;")  # 增大字体
        self.infoLab.setWordWrap(True)
        info_layout.addWidget(self.infoLab)

        scroll_layout.addWidget(self.info_card)

        # 操作按钮
        button_layout = QHBoxLayout()
        editBtn = QPushButton("编辑资料")
        editBtn.clicked.connect(self._edit_profile)
        editBtn.setStyleSheet("""
            QPushButton {
                background-color: #5cb85c;
                color: white;
                border: none;
                padding: 12px 24px;
                text-align: center;
                font-size: 18px;
                margin: 4px 2px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #4cae4c;
            }
        """)

        button_layout.addStretch()
        button_layout.addWidget(editBtn)
        button_layout.addStretch()
        scroll_layout.addLayout(button_layout)

        scroll_area.setWidget(scroll_content)
        lay.addWidget(scroll_area)

    # 添加清理内容的方法
    def _clear_content(self):
        """清理页面内容，用于用户登出时"""
        self.infoLab.setText("请先登录")

    def showEvent(self, event):
        self._refresh()

    def _refresh(self):
        if not self.main.user:
            self._clear_content()
            return
        self.client.send({"type": "profile", "user_id": self.main.user["UserID"]},
                         callback=self._on_profile)

    def _on_profile(self, resp):
        if resp.get("success"):
            u = resp["user"]
            # 权限组映射
            role_names = {
                1: "管理员",
                2: "VIP用户",
                3: "用户",
                4: "游客",
                5: "封禁"
            }

            # 获取权限组名称
            role_id = u.get('RoleID', 3)
            role_name = role_names.get(role_id, f"未知({role_id})")

            # 白名单状态
            white_state = '已通过' if u.get('WhiteState', 0) else '未通过'

            # QQ绑定状态
            qq_id = u.get('QQID') or '未绑定'

            # 格式化显示信息，加粗标签文本
            info_text = f"""
            <html>
            <head/>
            <body>
                <table border="0" style="margin: 10px; font-size: 18px;" cellspacing="10"> 
                    <tr>
                        <td><b style="font-weight: bold;">用户ID:</b></td>
                        <td style="font-weight: normal;">{u.get('UserID', '未知')}</td>
                    </tr>
                    <tr>
                        <td><b style="font-weight: bold;">用户名:</b></td>
                        <td style="font-weight: normal;">{u.get('Username', '未知')}</td>
                    </tr>
                    <tr>
                        <td><b style="font-weight: bold;">昵称:</b></td>
                        <td style="font-weight: normal;">{u.get('Nickname', '未知')}</td>
                    </tr>
                    <tr>
                        <td><b style="font-weight: bold;">邮箱:</b></td>
                        <td style="font-weight: normal;">{u.get('Email', '未知')}</td>
                    </tr>
                    <tr>
                        <td><b style="font-weight: bold;">手机:</b></td>
                        <td style="font-weight: normal;">{u.get('Phone', '未知')}</td>
                    </tr>
                    <tr>
                        <td><b style="font-weight: bold;">金币:</b></td>
                        <td style="color: #FFD700; font-weight: normal;">{u.get('Coins', 0)}</td>
                    </tr>
                    <tr>
                        <td><b style="font-weight: bold;">星星:</b></td>
                        <td style="color: #FFA500; font-weight: normal;">{u.get('Stars', 0)}</td>
                    </tr>
                    <tr>
                        <td><b style="font-weight: bold;">权限组:</b></td>
                        <td style="color: {'#FF0000' if role_id == 5 else '#00AA00' if role_id == 1 else '#0000FF'}; font-weight: normal;">
                            {role_name}
                        </td>
                    </tr>
                    <tr>
                        <td><b style="font-weight: bold;">白名单:</b></td>
                        <td style="color: {'#00AA00' if u.get('WhiteState', 0) else '#FF0000'}; font-weight: normal;">
                            {white_state}
                        </td>
                    </tr>
                    <tr>
                        <td><b style="font-weight: bold;">QQ绑定:</b></td>
                        <td style="font-weight: normal;">{qq_id}</td>
                    </tr>
                    <tr>
                        <td><b style="font-weight: bold;">姓名:</b></td>
                        <td style="font-weight: normal;">{u.get('FirstName', '未知')} {u.get('LastName', '未知')}</td>
                    </tr>
                    <tr>
                        <td><b style="font-weight: bold;">性别:</b></td>
                        <td style="font-weight: normal;">{u.get('Gender', '未知')}</td>
                    </tr>
                    <tr>
                        <td><b style="font-weight: bold;">生日:</b></td>
                        <td style="font-weight: normal;">{u.get('Birthday', '未知')}</td>
                    </tr>
                    <tr>
                        <td><b style="font-weight: bold;">简介:</b></td>
                        <td style="font-weight: normal;">{u.get('Bio', '无')}</td>
                    </tr>
                </table>
            </body>
            </html>
            """

            self.infoLab.setText(info_text)

    def _edit_profile(self):
        if not self.main.user:
            QMessageBox.warning(self, "提示", "请先登录")
            return
        self.client.send({"type": "profile", "user_id": self.main.user["UserID"]},
                         callback=self._on_edit_profile)

    def _on_edit_profile(self, resp):
        if resp.get("success"):
            dialog = EditProfileDialog(self.main, resp["user"])
            dialog.exec_()


# ========================= 编辑资料对话框 =========================
class EditProfileDialog(QDialog):
    def __init__(self, parent: MainWindow, user_data):
        super().__init__(parent, Qt.WindowCloseButtonHint)
        self.setWindowTitle("编辑个人资料")
        self.main = parent
        self.client = parent.client
        self.user_data = user_data
        self._init_ui()
        self._fill_data()

    def _init_ui(self):
        self.resize(400, 500)
        layout = QVBoxLayout(self)

        # 创建表单布局
        form_layout = QFormLayout()

        # 基本信息字段
        self.nickname_edit = QLineEdit()
        self.email_edit = QLineEdit()
        self.phone_edit = QLineEdit()
        self.first_name_edit = QLineEdit()
        self.last_name_edit = QLineEdit()
        self.gender_edit = QLineEdit()
        self.birthday_edit = QDateEdit()
        self.birthday_edit.setDisplayFormat("yyyy-MM-dd")
        self.birthday_edit.setCalendarPopup(True)
        self.bio_edit = QTextEdit()
        self.bio_edit.setMaximumHeight(100)

        # 添加字段到表单
        form_layout.addRow("昵称:", self.nickname_edit)
        form_layout.addRow("邮箱:", self.email_edit)
        form_layout.addRow("手机:", self.phone_edit)
        form_layout.addRow("名字:", self.first_name_edit)
        form_layout.addRow("姓氏:", self.last_name_edit)
        form_layout.addRow("性别:", self.gender_edit)
        form_layout.addRow("生日:", self.birthday_edit)
        form_layout.addRow("简介:", self.bio_edit)

        layout.addLayout(form_layout)

        # 按钮布局
        button_layout = QHBoxLayout()
        self.save_btn = QPushButton("保存")
        self.cancel_btn = QPushButton("取消")

        self.save_btn.clicked.connect(self._save_profile)
        self.cancel_btn.clicked.connect(self.reject)

        button_layout.addWidget(self.save_btn)
        button_layout.addWidget(self.cancel_btn)
        layout.addLayout(button_layout)

    def _fill_data(self):
        """填充用户数据到表单"""
        user = self.user_data

        # 填充基本信息
        self.nickname_edit.setText(user.get("Nickname", ""))
        self.email_edit.setText(user.get("Email", ""))
        self.phone_edit.setText(user.get("Phone", ""))

        # 填充个人资料信息
        self.first_name_edit.setText(user.get("FirstName", ""))
        self.last_name_edit.setText(user.get("LastName", ""))
        self.gender_edit.setText(user.get("Gender", ""))

        # 设置生日
        birthday_str = user.get("Birthday", "")
        if birthday_str:
            try:
                birthday = QDate.fromString(birthday_str, "yyyy-MM-dd")
                self.birthday_edit.setDate(birthday)
            except:
                pass  # 如果日期格式不正确，使用默认日期

        self.bio_edit.setPlainText(user.get("Bio", ""))

    def _save_profile(self):
        """保存用户资料"""
        # 构造请求数据
        req = {
            "type": "update_profile",
            "user_id": self.user_data["UserID"],
            "nickname": self.nickname_edit.text(),
            "email": self.email_edit.text(),
            "phone": self.phone_edit.text(),
            "first_name": self.first_name_edit.text(),
            "last_name": self.last_name_edit.text(),
            "gender": self.gender_edit.text(),
            "birthday": self.birthday_edit.date().toString("yyyy-MM-dd"),
            "bio": self.bio_edit.toPlainText()
        }

        # 发送更新请求
        self.client.send(req, callback=self._on_save_result)

    def _on_save_result(self, resp):
        """处理保存结果"""
        if resp.get("success"):
            QMessageBox.information(self, "成功", "资料更新成功！")
            self.accept()
            # 刷新主页面的资料展示
            self.main.stack.currentWidget()._refresh()
        else:
            QMessageBox.warning(self, "失败", f"更新失败：{resp.get('message')}")


# ========================= 白名单申请页面 =========================
class WhitelistPage(BasePage):
    def __init__(self, parent):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        self.applyBtn = QPushButton("提交白名单申请")
        self.applyBtn.clicked.connect(self._apply)
        lay.addWidget(self.applyBtn)

        # 添加白名单申请列表
        self.applicationList = QListWidget()
        lay.addWidget(QLabel("我的申请记录:"))
        lay.addWidget(self.applicationList)

        refreshBtn = QPushButton("刷新申请记录")
        refreshBtn.clicked.connect(self._refresh)
        lay.addWidget(refreshBtn)

        self._refresh()

    def _apply(self):
        if not self.main.user:
            QMessageBox.warning(self, "提示", "请先登录")
            return

        # 检查用户是否已经通过白名单审核
        if self.main.user.get("WhiteState", 0) == 1:
            QMessageBox.warning(self, "提示", "您已通过白名单审核，无需再次申请")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("白名单申请")
        v = QFormLayout(dlg)
        nameEdit = QLineEdit()
        genuineBox = QComboBox()
        genuineBox.addItems(["正版", "离线"])
        reasonEdit = QTextEdit()
        v.addRow("游戏名", nameEdit)
        v.addRow("账号类型", genuineBox)
        v.addRow("申请理由", reasonEdit)
        box = QHBoxLayout()
        ok = QPushButton("提交")
        ok.clicked.connect(lambda: self._submit(nameEdit.text(),
                                                1 if genuineBox.currentText() == "正版" else 0,
                                                reasonEdit.toPlainText(), dlg))
        box.addWidget(ok)
        box.addWidget(QPushButton("取消", clicked=dlg.reject))
        v.addRow(box)
        dlg.exec_()

    def _submit(self, name, gen, reason, dlg):
        req = {"type": "whitelist_apply", "user_id": self.main.user["UserID"],
               "playername": name, "genuine": gen, "reason": reason}
        self.client.send(req, callback=self._on_submit_result)
        dlg.accept()

    def _on_submit_result(self, resp):
        QMessageBox.information(
            self, "结果", "申请已提交！" if resp.get("success") else f"申请失败：{resp.get('message')}")
        self._refresh()  # 提交后刷新列表

    def _refresh(self):
        if not self.main.user:
            return
        self.client.send({"type": "get_user_whitelist_applications",
                          "user_id": self.main.user["UserID"]},
                         callback=self._on_applications_received)

    def _on_applications_received(self, resp):
        self.applicationList.clear()
        if resp.get("success"):
            applications = resp.get("applications", [])
            for app in applications:
                item_text = f"申请时间: {app['date']} | 玩家名: {app['playername']} | 状态: {app['status']}"
                item = QListWidgetItem(item_text)
                self.applicationList.addItem(item)

            # 根据申请状态更新申请按钮的可用性
            can_apply = True
            has_pending = False
            passed_count = 0

            for app in applications:
                if app["status"] == "待审核":
                    has_pending = True
                elif app["status"] == "已通过":
                    passed_count += 1

            # 如果已通过白名单或有待审核的申请，则禁用申请按钮
            if passed_count > 0 or has_pending:
                can_apply = False

            self.applyBtn.setEnabled(can_apply)

            # 更新主窗口用户信息
            if passed_count > 0 and self.main.user:
                self.main.user["WhiteState"] = 1
                self.main._update_navbar_visibility()


# ========================= QQ绑定页面 =========================
class QQPage(BasePage):
    def __init__(self, parent):
        super().__init__(parent)
        lay = QVBoxLayout(self)

        # 添加标题标签
        title_label = QLabel("QQ绑定")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("""
            font-size: 24px;
            font-weight: bold;
            color: #333;
            margin: 20px;
        """)
        lay.addWidget(title_label)

        # 添加暂未开放的提示信息
        notice_label = QLabel("暂未开放")
        notice_label.setAlignment(Qt.AlignCenter)
        notice_label.setStyleSheet("""
            font-size: 32px;
            font-weight: bold;
            color: #999;
            margin: 50px;
            padding: 20px;
            border: 2px dashed #ccc;
            border-radius: 10px;
            background-color: #f9f9f9;
        """)
        lay.addWidget(notice_label)

        # 添加说明文字
        description_label = QLabel("QQ绑定功能正在开发中，敬请期待！")
        description_label.setAlignment(Qt.AlignCenter)
        description_label.setStyleSheet("""
            font-size: 16px;
            color: #666;
            margin: 10px;
        """)
        lay.addWidget(description_label)

        # 禁用原来的绑定按钮
        self.bindBtn = QPushButton("绑定 QQ")
        self.bindBtn.clicked.connect(self._bind)
        self.bindBtn.setEnabled(False)  # 禁用按钮
        self.bindBtn.setStyleSheet("""
            QPushButton {
                background-color: #ccc;
                color: #999;
                border: 1px solid #999;
                padding: 10px;
                font-size: 16px;
                border-radius: 5px;
            }
        """)
        lay.addWidget(self.bindBtn)

    def _bind(self):
        if not self.main.user:
            QMessageBox.warning(self, "提示", "请先登录")
            return
        qq, ok = QInputDialog.getText(self, "绑定 QQ", "请输入 QQ 号：")
        if ok and qq:
            self.client.send({"type": "bind_qq", "user_id": self.main.user["UserID"], "qq": qq},
                             callback=lambda r: QMessageBox.information(
                                 self, "结果", "绑定成功！" if r.get("success") else r.get("message")))


# ========================= 管理员面板 =========================
class AdminPage(BasePage):
    def __init__(self, parent):
        super().__init__(parent)

        # 初始化分页相关变量
        self.current_page = 1
        self.total_pages = 1
        self.page_size = 10

        # 创建标签页控件
        self.tabWidget = QTabWidget()
        self.userManagementTab = QWidget()
        self.whitelistApprovalTab = QWidget()

        self.tabWidget.addTab(self.userManagementTab, "用户管理")
        self.tabWidget.addTab(self.whitelistApprovalTab, "白名单审批")

        # 设置用户管理标签页
        userLayout = QVBoxLayout(self.userManagementTab)
        userLayout.addWidget(QLabel("管理员控制台——所有用户"))
        self.table = QTableWidget(0, 12)
        self.table.setHorizontalHeaderLabels([
            "UID", "用户名", "昵称", "邮箱", "角色", "玩家名",
            "游玩方式", "白名单状态", "通过时间", "QQ号", "在线状态", "操作"
        ])
        # 设置列宽以适应内容
        self.table.setColumnWidth(0, 80)  # UID
        self.table.setColumnWidth(1, 100)  # 用户名
        self.table.setColumnWidth(2, 100)  # 昵称
        self.table.setColumnWidth(3, 150)  # 邮箱
        self.table.setColumnWidth(4, 80)  # 角色
        self.table.setColumnWidth(5, 100)  # 玩家名
        self.table.setColumnWidth(6, 80)  # 游玩方式
        self.table.setColumnWidth(7, 100)  # 白名单状态
        self.table.setColumnWidth(8, 100)  # 通过时间
        self.table.setColumnWidth(9, 120)  # QQ号
        self.table.setColumnWidth(10, 80)  # 在线状态
        self.table.setColumnWidth(11, 250)  # 操作

        # 添加单元格点击事件连接
        self.table.cellClicked.connect(self._on_cell_clicked)

        userLayout.addWidget(self.table)

        # 添加分页控件
        self.paginationWidget = QWidget()
        paginationLayout = QHBoxLayout(self.paginationWidget)

        self.firstPageBtn = QPushButton("首页")
        self.prevPageBtn = QPushButton("上一页")
        self.nextPageBtn = QPushButton("下一页")
        self.lastPageBtn = QPushButton("末页")

        self.pageInfoLabel = QLabel()
        self.pageInput = QLineEdit()
        self.pageInput.setFixedWidth(50)
        self.jumpBtn = QPushButton("跳转")

        paginationLayout.addWidget(self.firstPageBtn)
        paginationLayout.addWidget(self.prevPageBtn)
        paginationLayout.addWidget(self.pageInfoLabel)
        paginationLayout.addWidget(self.nextPageBtn)
        paginationLayout.addWidget(self.lastPageBtn)
        paginationLayout.addStretch()
        paginationLayout.addWidget(QLabel("跳转到:"))
        paginationLayout.addWidget(self.pageInput)
        paginationLayout.addWidget(self.jumpBtn)

        userLayout.addWidget(self.paginationWidget)

        # 连接分页按钮信号
        self.firstPageBtn.clicked.connect(self._first_page)
        self.prevPageBtn.clicked.connect(self._prev_page)
        self.nextPageBtn.clicked.connect(self._next_page)
        self.lastPageBtn.clicked.connect(self._last_page)
        self.jumpBtn.clicked.connect(self._jump_to_page)

        # 设置白名单审批标签页
        whitelistLayout = QVBoxLayout(self.whitelistApprovalTab)
        self.whitelistTable = QTableWidget(0, 6)
        self.whitelistTable.setHorizontalHeaderLabels(["申请时间", "用户ID", "玩家名", "账号类型", "申请理由", "操作"])
        # 设置白名单表格列宽
        self.whitelistTable.setColumnWidth(0, 120)  # 申请时间
        self.whitelistTable.setColumnWidth(1, 80)  # 用户ID
        self.whitelistTable.setColumnWidth(2, 100)  # 玩家名
        self.whitelistTable.setColumnWidth(3, 80)  # 账号类型
        self.whitelistTable.setColumnWidth(4, 200)  # 申请理由
        self.whitelistTable.setColumnWidth(5, 200)  # 操作
        whitelistLayout.addWidget(QLabel("待审批的白名单申请:"))
        whitelistLayout.addWidget(self.whitelistTable)

        refreshWhitelistBtn = QPushButton("刷新白名单申请")
        refreshWhitelistBtn.clicked.connect(self._load_whitelist_applications)
        whitelistLayout.addWidget(refreshWhitelistBtn)

        # 主布局
        lay = QVBoxLayout(self)
        lay.addWidget(self.tabWidget)

        # 添加标签页切换信号连接
        self.tabWidget.currentChanged.connect(self._on_tab_changed)

    def showEvent(self, event):
        # 页面显示时加载数据
        self._load_page_count()

    def _load_page_count(self):
        """加载用户总数并计算分页信息"""
        self.client.send({"type": "get_users_count"}, callback=self._on_page_count_received)

    def _on_page_count_received(self, resp):
        """收到用户总数后的回调"""
        if resp.get("success"):
            user_count = resp.get("count", 0)
            self.total_pages = (user_count + self.page_size - 1) // self.page_size  # 向上取整
            self.current_page = 1  # 重置到第一页
            self._load_current_page()
        else:
            QMessageBox.warning(self, "失败", f"获取用户数量失败: {resp.get('message', '未知错误')}")

    def _load_current_page(self):
        """加载当前页数据"""
        self.client.send({
            "type": "get_users_by_page",
            "page": self.current_page,
            "page_size": self.page_size
        }, callback=self._on_users_page_received)

    def _on_users_page_received(self, resp):
        """收到分页用户数据后的回调"""
        if resp.get("success"):
            self._fill(resp["data"])
            self._update_pagination_info()
        else:
            QMessageBox.warning(self, "失败", f"获取用户数据失败: {resp.get('message', '未知错误')}")

    def _update_pagination_info(self):
        """更新分页信息显示"""
        self.pageInfoLabel.setText(f"第 {self.current_page} 页，共 {self.total_pages} 页")

        # 更新按钮状态
        self.firstPageBtn.setEnabled(self.current_page > 1)
        self.prevPageBtn.setEnabled(self.current_page > 1)
        self.nextPageBtn.setEnabled(self.current_page < self.total_pages)
        self.lastPageBtn.setEnabled(self.current_page < self.total_pages)

    def _first_page(self):
        """首页"""
        if self.current_page != 1:
            self.current_page = 1
            self._load_current_page()

    def _prev_page(self):
        """上一页"""
        if self.current_page > 1:
            self.current_page -= 1
            self._load_current_page()

    def _next_page(self):
        """下一页"""
        if self.current_page < self.total_pages:
            self.current_page += 1
            self._load_current_page()

    def _last_page(self):
        """末页"""
        if self.current_page != self.total_pages:
            self.current_page = self.total_pages
            self._load_current_page()

    def _jump_to_page(self):
        """跳转到指定页"""
        try:
            page = int(self.pageInput.text())
            if 1 <= page <= self.total_pages:
                self.current_page = page
                self._load_current_page()
            else:
                QMessageBox.warning(self, "无效页码", f"请输入1到{self.total_pages}之间的页码")
        except ValueError:
            QMessageBox.warning(self, "无效输入", "请输入有效的页码数字")

    def _fill(self, users):
        self.table.setRowCount(len(users))
        for i, u in enumerate(users):
            # 填充用户基本信息 (设置为只读)
            # 修复：添加默认值以防止 None 值导致崩溃
            uid_item = QTableWidgetItem(str(u.get("UserID", "") or ""))
            uid_item.setFlags(uid_item.flags() & ~Qt.ItemIsEditable)  # 设置为只读
            self.table.setItem(i, 0, uid_item)

            username_item = QTableWidgetItem(u.get("Username", "") or "")
            username_item.setFlags(username_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, 1, username_item)

            nickname_item = QTableWidgetItem(u.get("Nickname", "") or "")
            nickname_item.setFlags(nickname_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, 2, nickname_item)

            email_item = QTableWidgetItem(u.get("Email", "") or "")
            email_item.setFlags(email_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, 3, email_item)

            # 角色名称 - 根据RoleID显示正确的角色名称
            role_id = u.get("RoleID", 3)  # 默认为用户(3)
            # 修复：确保 role_id 是有效值
            if role_id is None:
                role_id = 3
            role_names = {1: "管理员", 2: "VIP用户", 3: "用户", 4: "访客", 5: "封禁"}
            role_name = role_names.get(role_id, f"未知({role_id})")
            role_item = QTableWidgetItem(role_name)
            role_item.setFlags(role_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, 4, role_item)

            # 玩家信息
            playername_item = QTableWidgetItem(u.get("PlayerName", "") or "")
            playername_item.setFlags(playername_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, 5, playername_item)

            # 游玩方式
            genuine = "正版" if u.get("Genuine", 0) == 1 else "离线"
            genuine_item = QTableWidgetItem(genuine)
            genuine_item.setFlags(genuine_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, 6, genuine_item)

            # 白名单状态
            white_state = "已通过" if u.get("WhiteState", 0) == 1 else "未通过"
            white_state_item = QTableWidgetItem(white_state)
            white_state_item.setFlags(white_state_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, 7, white_state_item)

            # 通过时间
            pass_date = u.get("PassDate", "") or ""
            pass_date_item = QTableWidgetItem(pass_date)
            pass_date_item.setFlags(pass_date_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, 8, pass_date_item)

            # QQ号
            qq_id = u.get("QQID", "") or ""
            qq_item = QTableWidgetItem(qq_id)
            qq_item.setFlags(qq_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, 9, qq_item)

            # 添加在线状态列
            online_status = "在线" if u.get("online", False) else "离线"
            online_color = "green" if u.get("online", False) else "red"
            online_item = QTableWidgetItem(online_status)
            online_item.setFlags(online_item.flags() & ~Qt.ItemIsEditable)
            online_item.setForeground(QColor(online_color))
            self.table.setItem(i, 10, online_item)  # 插入到操作列之前

            # 操作按钮
            w = QWidget()
            h = QHBoxLayout(w)
            h.setSpacing(10)  # 增加按钮间距

            # 只显示升级/降级按钮
            # 修复：确保 role_id 是有效数字后再比较
            try:
                role_id_int = int(role_id)
                if role_id_int > 1:  # 不是最高权限才能升级
                    upBtn = QPushButton("升级")
                    upBtn.setFixedSize(60, 30)  # 增大按钮尺寸
                    # 修复：使用functools.partial避免lambda捕获问题
                    # 修复：确保 UserID 存在
                    if "UserID" in u:
                        upBtn.clicked.connect(partial(self._role, u["UserID"], role_id_int - 1))
                        h.addWidget(upBtn)

                if role_id_int < 5:  # 不是最低权限才能降级
                    downBtn = QPushButton("降级")
                    downBtn.setFixedSize(60, 30)  # 增大按钮尺寸
                    # 修复：使用functools.partial避免lambda捕获问题
                    # 修复：确保 UserID 存在
                    if "UserID" in u:
                        downBtn.clicked.connect(partial(self._role, u["UserID"], role_id_int + 1))
                        h.addWidget(downBtn)
            except (ValueError, TypeError):
                # 如果 role_id 无法转换为整数，则不显示操作按钮
                pass

            h.setContentsMargins(5, 5, 5, 5)
            w.setLayout(h)
            self.table.setCellWidget(i, 11, w)  # 操作列索引更新为11

    # 添加单元格点击事件处理函数
    def _on_cell_clicked(self, row, column):
        """处理表格单元格点击事件，显示完整信息"""
        try:
            # 获取点击的单元格项
            item = self.table.item(row, column)
            if item is None:
                return

            # 获取单元格文本
            text = item.text()
            if not text:
                return

            # 定义列名映射
            column_names = {
                0: "用户ID",
                1: "用户名",
                2: "昵称",
                3: "邮箱",
                4: "角色",
                5: "玩家名",
                6: "游玩方式",
                7: "白名单状态",
                8: "通过时间",
                9: "QQ号",
                10: "在线状态"
            }

            # 获取列名
            column_name = column_names.get(column, f"列{column}")

            # 创建信息展示对话框
            dialog = QDialog(self)
            dialog.setWindowTitle(f"{column_name} 详细信息")
            dialog.setFixedSize(400, 200)

            layout = QVBoxLayout(dialog)

            # 添加信息标签
            info_label = QLabel(f"{column_name}:")
            info_label.setStyleSheet("font-weight: bold; font-size: 14px;")
            layout.addWidget(info_label)

            # 添加文本显示区域
            text_edit = QTextEdit()
            text_edit.setPlainText(text)
            text_edit.setReadOnly(True)
            layout.addWidget(text_edit)

            # 添加关闭按钮
            close_btn = QPushButton("关闭")
            close_btn.clicked.connect(dialog.close)
            layout.addWidget(close_btn)

            dialog.exec_()

        except Exception as e:
            print(f"显示单元格信息时出错: {e}")

    def _role(self, uid, new_role_id):
        # 发送权限更新请求
        self.client.send({"type": "update_role", "user_id": uid, "role_id": new_role_id},
                         callback=lambda r: self._on_operation_complete(r, "权限更新"))

    def _on_operation_complete(self, resp, operation_name):
        if resp.get("success"):
            QMessageBox.information(self, "成功", f"{operation_name}成功")
            self._load_current_page()  # 刷新当前页
        else:
            QMessageBox.warning(self, "失败", f"{operation_name}失败: {resp.get('message', '未知错误')}")

    def _on_tab_changed(self, index):
        """标签页切换时的处理函数"""
        if index == 1:  # 白名单审批标签页
            self._load_whitelist_applications()

    def _load_whitelist_applications(self):
        self.client.send({"type": "get_all_whitelist_applications"},
                         callback=self._on_whitelist_applications_received)

    def _on_whitelist_applications_received(self, resp):
        self.whitelistTable.setRowCount(0)  # 清空表格
        if resp.get("success"):
            applications = resp.get("applications", [])
            self.whitelistTable.setRowCount(len(applications))
            for i, app in enumerate(applications):
                # 申请时间
                date_item = QTableWidgetItem(app.get("date", "") or "")
                date_item.setFlags(date_item.flags() & ~Qt.ItemIsEditable)
                self.whitelistTable.setItem(i, 0, date_item)

                # 用户ID
                user_id_item = QTableWidgetItem(str(app.get("user_id", "") or ""))
                user_id_item.setFlags(user_id_item.flags() & ~Qt.ItemIsEditable)
                self.whitelistTable.setItem(i, 1, user_id_item)

                # 玩家名
                playername_item = QTableWidgetItem(app.get("playername", "") or "")
                playername_item.setFlags(playername_item.flags() & ~Qt.ItemIsEditable)
                self.whitelistTable.setItem(i, 2, playername_item)

                # 账号类型 - 从内容中解析
                content = app.get("content", "")
                genuine_text = "未知"
                if "正版" in content:
                    genuine_text = "正版"
                elif "离线" in content:
                    genuine_text = "离线"
                genuine_item = QTableWidgetItem(genuine_text)
                genuine_item.setFlags(genuine_item.flags() & ~Qt.ItemIsEditable)
                self.whitelistTable.setItem(i, 3, genuine_item)

                # 申请理由 - 从内容中解析
                reason_text = content
                if reason_text.startswith("申请人ID:"):
                    # 提取申请介绍部分
                    lines = reason_text.split("\n")
                    for line in lines:
                        if line.startswith("申请介绍："):
                            reason_text = line[6:]  # 去掉"申请介绍："前缀
                            break
                reason_item = QTableWidgetItem(reason_text)
                reason_item.setFlags(reason_item.flags() & ~Qt.ItemIsEditable)
                self.whitelistTable.setItem(i, 4, reason_item)

                # 添加操作按钮
                btnWidget = QWidget()
                btnLayout = QHBoxLayout(btnWidget)
                btnLayout.setSpacing(10)  # 增加按钮间距

                # 只对状态为"待审核"的申请显示操作按钮
                if app.get("status") == "待审核":
                    approveBtn = QPushButton("同意")
                    approveBtn.setFixedSize(60, 30)  # 增大按钮尺寸
                    rejectBtn = QPushButton("拒绝")
                    rejectBtn.setFixedSize(60, 30)  # 增大按钮尺寸

                    # 修复：使用functools.partial避免lambda捕获问题
                    # 修复：确保必要字段存在
                    date = app.get("date")
                    user_id = app.get("user_id")
                    playername = app.get("playername")
                    if date is not None and user_id is not None:
                        approveBtn.clicked.connect(
                            partial(self._process_whitelist_application, date, user_id, playername, True))
                        rejectBtn.clicked.connect(
                            partial(self._process_whitelist_application, date, user_id, playername, False))

                        btnLayout.addWidget(approveBtn)
                        btnLayout.addWidget(rejectBtn)
                    else:
                        # 如果缺少必要信息，显示错误信息
                        errorLabel = QLabel("数据不完整")
                        btnLayout.addWidget(errorLabel)
                else:
                    # 显示已处理状态
                    statusLabel = QLabel(app.get("status", "已处理"))
                    btnLayout.addWidget(statusLabel)

                btnLayout.setContentsMargins(5, 5, 5, 5)
                btnWidget.setLayout(btnLayout)
                self.whitelistTable.setCellWidget(i, 5, btnWidget)

    def _process_whitelist_application(self, date, user_id, playername, approved):
        self.client.send({
            "type": "process_whitelist_application",
            "date": date,
            "user_id": user_id,
            "playername": playername,
            "approved": approved
        }, callback=lambda r: self._on_application_processed(r, date, user_id, approved))

    def _on_application_processed(self, resp, date, user_id, approved):
        if resp.get("success"):
            QMessageBox.information(self, "成功", "白名单申请已处理")
            self._load_whitelist_applications()  # 刷新列表
        else:
            QMessageBox.warning(self, "失败", resp.get("message"))


# ========================= 传声筒页面 =========================
class MessagePage(BasePage):
    def __init__(self, parent):
        super().__init__(parent)
        self.contacts = []  # 联系人列表
        self.current_contact = None  # 当前选中的联系人
        self.gift_info = {"coins_given_today": 0, "stars_given_today": 0, "coin_limit": 5, "star_limit": 1}  # 赠与信息
        self.gift_dialog = None  # 添加gift_dialog属性
        self.online_users = set()  # 在线用户集合
        self.unread_counts = {}  # 未读消息计数 {contact_id: count}
        self._init_ui()
        self._bind_signal()  # 绑定信号
        self._refresh_contacts()

    def _init_ui(self):
        layout = QHBoxLayout(self)

        # 左侧联系人列表
        left_panel = QVBoxLayout()
        self.contact_list = QListWidget()
        self.contact_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.contact_list.customContextMenuRequested.connect(self._show_contact_context_menu)
        self.contact_list.itemClicked.connect(self._on_contact_selected)
        refresh_btn = QPushButton("刷新联系人")
        refresh_btn.clicked.connect(self._refresh_contacts)
        # 添加"添加联系人"按钮
        add_contact_btn = QPushButton("添加联系人")
        add_contact_btn.clicked.connect(self._add_contact)

        left_panel.addWidget(QLabel("联系人:"))
        left_panel.addWidget(self.contact_list)
        left_panel.addWidget(refresh_btn)
        left_panel.addWidget(add_contact_btn)  # 添加按钮到界面

        # 右侧消息区域
        right_panel = QVBoxLayout()
        # 添加在线状态显示
        self.online_status_label = QLabel()
        self.online_status_label.setAlignment(Qt.AlignLeft)
        self.online_status_label.setStyleSheet("color: green; font-weight: bold;")
        self.online_status_label.hide()

        # 修改：添加显示当前聊天对象UserID的标签
        self.contact_info_label = QLabel()
        self.contact_info_label.setAlignment(Qt.AlignRight)
        self.message_display = QTextEdit()
        self.message_display.setReadOnly(True)
        # 移除气泡样式，使用默认样式
        self.message_display.setStyleSheet("""
            QTextEdit {
                background-color: #ffffff;
                border: 1px solid #e0e0e0;
                border-radius: 5px;
                font-family: "Microsoft YaHei", sans-serif;
                font-size: 14px;
                line-height: 1.5;
            }
        """)
        self.message_input = QTextEdit()
        self.message_input.setMaximumHeight(100)
        send_btn = QPushButton("发送")
        send_btn.clicked.connect(self._send_message)
        right_panel.addWidget(self.online_status_label)  # 添加在线状态标签
        right_panel.addWidget(self.contact_info_label)  # 添加到界面
        right_panel.addWidget(QLabel("消息记录:"))
        right_panel.addWidget(self.message_display)
        right_panel.addWidget(QLabel("输入消息:"))
        right_panel.addWidget(self.message_input)
        right_panel.addWidget(send_btn)
        # 添加到主布局
        left_widget = QWidget()
        left_widget.setLayout(left_panel)
        left_widget.setFixedWidth(200)

        right_widget = QWidget()
        right_widget.setLayout(right_panel)

        layout.addWidget(left_widget)
        layout.addWidget(right_widget)

    def _refresh_contacts(self):
        if not self.main.user:
            return
        self.client.send({
            "type": "get_contacts",
            "user_id": self.main.user["UserID"]
        }, callback=self._on_contacts_received)

    def _on_contact_selected(self, item):
        """处理联系人选择"""
        if not item:
            return

        contact = item.data(Qt.UserRole)
        if not contact:
            return

        self.current_contact = contact
        # 修改：更新联系人信息显示
        self._update_contact_info()
        # 重置该联系人的未读消息数
        contact_id = str(contact["UserID"])
        if contact_id in self.unread_counts:
            old_unread = self.unread_counts[contact_id]
            self.unread_counts[contact_id] = 0
            self.main._update_unread_count(-old_unread)  # 更新总未读数
            self._refresh_contact_list()  # 刷新联系人列表显示

        # 当用户查看聊天界面时，标记与该联系人的所有消息为已读
        if self.main.user and self.current_contact:
            self.client.send({
                "type": "mark_messages_as_read",
                "user_id": self.main.user["UserID"],
                "contact_id": self.current_contact["UserID"]
            }, callback=self._on_messages_marked_as_read)

        self._load_messages()
        self._update_online_status()  # 更新在线状态显示

    def _on_messages_marked_as_read(self, resp):
        """处理消息标记为已读的响应"""
        if resp.get("success"):
            # 更新未读消息计数
            self.main.unread_count = resp.get("unread_count", 0)
            self.main._update_unread_count(0)  # 刷新显示
            self.unread_counts = resp.get("unread_details", {})
            self._refresh_contact_list()

    def _refresh_contact_list(self):
        """刷新联系人列表显示"""
        self.contact_list.clear()

        # 过滤掉全是不可见消息的联系人
        filtered_contacts = []
        for contact in self.contacts:
            # 检查与该联系人是否有可见消息
            has_visible_messages = self._has_visible_messages_with_contact(contact["UserID"])
            if has_visible_messages:
                filtered_contacts.append(contact)

        for contact in filtered_contacts:
            # 显示备注名（如果存在）或者默认显示用户名和昵称
            # 修改显示格式为"备注（昵称）"或"用户名（昵称）"
            if contact.get('remark') and contact['remark'] != contact['Nickname']:
                display_name = f"{contact['remark']} ({contact['Nickname']})"
            else:
                display_name = contact['Nickname']

            # 添加未读消息数显示，确保最少显示0
            contact_id = str(contact["UserID"])
            unread_count = self.unread_counts.get(contact_id, 0)
            display_name += f" ({unread_count})"  # 始终显示未读数，即使为0

            item = QListWidgetItem(display_name)
            item.setData(Qt.UserRole, contact)
            # 根据在线状态设置背景色
            if contact["UserID"] in self.online_users:
                item.setBackground(QColor("#e0ffe0"))  # 浅绿色表示在线
            self.contact_list.addItem(item)

    def _on_contacts_received(self, resp):
        if resp.get("success"):
            self.contacts = resp["contacts"]
            # 更新在线用户列表
            if "online_users" in resp:
                self.online_users = set(resp["online_users"])
            self._refresh_contact_list()
            # 加载服务器保存的联系人备注
            self._load_server_remarks()
        else:
            QMessageBox.warning(self, "获取联系人失败", resp.get("message"))

    def _load_server_remarks(self):
        """从服务器加载联系人备注"""
        # 实际上备注已经在 get_contacts 中返回，这里不需要额外处理
        pass

    def _update_online_status(self):
        """更新在线状态显示"""
        if self.current_contact and self.current_contact["UserID"] in self.online_users:
            self.online_status_label.setText("● 对方在线")
            self.online_status_label.show()
        else:
            self.online_status_label.hide()

    def _load_messages(self):
        if not self.main.user or not self.current_contact:
            return
        self.client.send({
            "type": "get_messages",
            "user_id": self.main.user["UserID"],
            "contact_id": self.current_contact["UserID"]
        }, callback=self._on_messages_received)

    def _on_messages_received(self, resp):
        if resp.get("success"):
            messages = resp["messages"]
            self.message_display.clear()

            # 格式化显示消息
            self._display_messages(messages)

            # 滚动到底部
            self.message_display.moveCursor(QTextCursor.End)
        else:
            QMessageBox.warning(self, "获取消息失败", resp.get("message"))

    def _display_messages(self, messages):
        """格式化显示消息，移除气泡样式"""
        if not messages:
            return

        # 清空显示区域
        self.message_display.clear()

        # 设置文本格式
        text_cursor = self.message_display.textCursor()
        text_format = QTextCharFormat()

        # 用于跟踪时间分组
        last_time = None

        for msg in messages:
            # 解析时间
            try:
                msg_time = datetime.datetime.strptime(msg['timestamp'], '%Y-%m-%d %H:%M:%S')
            except ValueError:
                # 如果时间格式不正确，使用消息中的原始时间字符串或当前时间
                # 修复：优先使用消息中的原始时间字符串
                try:
                    msg_time = datetime.datetime.fromisoformat(msg['timestamp'])
                except:
                    msg_time = datetime.datetime.now()

            # 显示时间分隔符（如果需要）
            if not last_time or (msg_time - last_time).total_seconds() > 300:  # 5分钟间隔
                time_format = QTextCharFormat()
                time_format.setForeground(QColor("#999999"))
                time_format.setFontPointSize(9)

                time_block_format = QTextBlockFormat()
                time_block_format.setAlignment(Qt.AlignCenter)

                text_cursor.insertBlock(time_block_format)
                text_cursor.insertText(msg_time.strftime('%Y-%m-%d %H:%M'), time_format)
                text_cursor.insertBlock()

                last_time = msg_time

            # 创建消息块格式
            block_format = QTextBlockFormat()

            # 根据消息类型设置样式
            if msg['sender_id'] == "0":  # 系统/服务器消息
                block_format.setAlignment(Qt.AlignCenter)
                text_format.setForeground(QColor("#666666"))
                text_format.setFontItalic(True)

                # 插入系统消息
                text_cursor.insertBlock(block_format)

                # 处理富文本消息（如赠与消息）
                if msg['content'].startswith("**") and msg['content'].endswith("**"):
                    # 这是富文本消息
                    content = msg['content'][2:-2]  # 去掉**标记
                    text_cursor.insertHtml(content)
                else:
                    # 普通文本消息
                    text_cursor.insertText(msg['content'], text_format)

                text_cursor.insertBlock()
            elif msg['content'].startswith("**") and msg['content'].endswith("**"):
                # 新增：处理赠与消息（服务器消息类型）
                block_format.setAlignment(Qt.AlignCenter)
                text_format.setFontWeight(QFont.Bold)  # 加粗

                # 插入赠与消息
                text_cursor.insertBlock(block_format)

                # 处理富文本消息
                content = msg['content'][2:-2]  # 去掉**标记
                text_cursor.insertHtml(content)

                text_cursor.insertBlock()
            elif str(msg['sender_id']) == str(self.main.user["UserID"]):  # 当前用户发送的消息
                block_format.setAlignment(Qt.AlignRight)
                text_format.setForeground(QColor("#0084ff"))  # 蓝色文字
                text_format.setFontWeight(QFont.Normal)  # 恢复正常字体粗细

                # 插入消息内容
                text_cursor.insertBlock(block_format)

                # 处理富文本消息（如赠与消息）
                if msg['content'].startswith("**") and msg['content'].endswith("**"):
                    # 这是富文本消息
                    content = msg['content'][2:-2]  # 去掉**标记
                    text_cursor.insertHtml(content)
                else:
                    # 普通文本消息
                    text_cursor.insertText(msg['content'], text_format)

                text_cursor.insertBlock()
            else:  # 对方发送的消息
                block_format.setAlignment(Qt.AlignLeft)
                text_format.setForeground(QColor("#000000"))
                text_format.setFontWeight(QFont.Normal)  # 恢复正常字体粗细

                # 插入消息内容
                text_cursor.insertBlock(block_format)

                # 处理富文本消息（如赠与消息）
                if msg['content'].startswith("**") and msg['content'].endswith("**"):
                    # 这是富文本消息
                    content = msg['content'][2:-2]  # 去掉**标记
                    text_cursor.insertHtml(content)
                else:
                    # 普通文本消息
                    text_cursor.insertText(msg['content'], text_format)

                text_cursor.insertBlock()

    def _display_messages_append(self, messages):
        """追加显示消息"""
        if not messages:
            return

        # 获取文本光标位置
        text_cursor = self.message_display.textCursor()
        text_cursor.movePosition(QTextCursor.End)

        # 设置文本格式
        text_format = QTextCharFormat()

        # 获取最后一条消息的时间作为last_time
        last_time = None
        try:
            # 获取当前显示区域的最后一行时间戳
            document = self.message_display.document()
            block = document.lastBlock()
            while block.isValid():
                text = block.text().strip()
                # 检查是否是时间格式 (YYYY-MM-DD HH:MM)
                if len(text) == 16 and text[4] == '-' and text[7] == '-' and text[10] == ' ' and text[13] == ':':
                    try:
                        last_time = datetime.datetime.strptime(text, '%Y-%m-%d %H:%M')
                        break
                    except:
                        pass
                block = block.previous()
        except:
            pass

        for msg in messages:
            # 解析时间
            try:
                msg_time = datetime.datetime.strptime(msg['timestamp'], '%Y-%m-d %H:%M:%S')
            except ValueError:
                # 如果时间格式不正确，使用当前时间
                msg_time = datetime.datetime.now()

            # 显示时间分隔符（如果需要）
            show_time = False
            if not last_time:
                show_time = True
            elif (msg_time - last_time).total_seconds() > 300:  # 5分钟间隔
                show_time = True

            if show_time:
                time_format = QTextCharFormat()
                time_format.setForeground(QColor("#999999"))
                time_format.setFontPointSize(9)

                time_block_format = QTextBlockFormat()
                time_block_format.setAlignment(Qt.AlignCenter)

                # 添加空行分隔
                if last_time:  # 不是第一条消息
                    text_cursor.insertBlock()

                text_cursor.insertBlock(time_block_format)
                text_cursor.insertText(msg_time.strftime('%Y-%m-%d %H:%M'), time_format)
                text_cursor.insertBlock()

                last_time = msg_time

            # 创建消息块格式
            block_format = QTextBlockFormat()

            # 根据消息类型设置样式
            if msg['sender_id'] == "0":  # 系统/服务器消息
                block_format.setAlignment(Qt.AlignCenter)
                text_format.setForeground(QColor("#666666"))
                text_format.setFontItalic(True)

                # 插入系统消息
                text_cursor.insertBlock(block_format)

                # 处理富文本消息（如赠与消息）
                if msg['content'].startswith("**") and msg['content'].endswith("**"):
                    # 这是富文本消息
                    content = msg['content'][2:-2]  # 去掉**标记
                    text_cursor.insertHtml(content)
                else:
                    # 普通文本消息
                    text_cursor.insertText(msg['content'], text_format)

                text_cursor.insertBlock()
            elif msg['content'].startswith("**") and msg['content'].endswith("**"):
                # 新增：处理赠与消息（服务器消息类型）
                block_format.setAlignment(Qt.AlignCenter)
                text_format.setFontWeight(QFont.Bold)  # 加粗

                # 插入赠与消息
                text_cursor.insertBlock(block_format)

                # 处理富文本消息
                content = msg['content'][2:-2]  # 去掉**标记
                text_cursor.insertHtml(content)

                text_cursor.insertBlock()
            elif str(msg['sender_id']) == str(self.main.user["UserID"]):  # 当前用户发送的消息
                block_format.setAlignment(Qt.AlignRight)
                text_format.setForeground(QColor("#0084ff"))  # 蓝色文字
                text_format.setFontWeight(QFont.Normal)  # 恢复正常字体粗细

                # 插入消息内容
                text_cursor.insertBlock(block_format)

                # 处理富文本消息（如赠与消息）
                if msg['content'].startswith("**") and msg['content'].endswith("**"):
                    # 这是富文本消息
                    content = msg['content'][2:-2]  # 去掉**标记
                    text_cursor.insertHtml(content)
                else:
                    # 普通文本消息
                    text_cursor.insertText(msg['content'], text_format)

                text_cursor.insertBlock()
            else:  # 对方发送的消息
                block_format.setAlignment(Qt.AlignLeft)
                text_format.setForeground(QColor("#000000"))
                text_format.setFontWeight(QFont.Normal)  # 恢复正常字体粗细

                # 插入消息内容
                text_cursor.insertBlock(block_format)

                # 处理富文本消息（如赠与消息）
                if msg['content'].startswith("**") and msg['content'].endswith("**"):
                    # 这是富文本消息
                    content = msg['content'][2:-2]  # 去掉**标记
                    text_cursor.insertHtml(content)
                else:
                    # 普通文本消息
                    text_cursor.insertText(msg['content'], text_format)

                text_cursor.insertBlock()

        # 设置光标
        self.message_display.setTextCursor(text_cursor)
        # 滚动到底部
        self.message_display.moveCursor(QTextCursor.End)

    def _send_message(self):
        if not self.main.user or not self.current_contact:
            QMessageBox.warning(self, "提示", "请选择联系人")
            return

        content = self.message_input.toPlainText().strip()
        if not content:
            QMessageBox.warning(self, "提示", "请输入消息内容")
            return

        # 添加消息长度检查，限制为250个字符
        if len(content) > 250:
            QMessageBox.warning(self, "提示", "消息长度不能超过250个字符")
            return

        # 检查是否是发送给自己
        if str(self.current_contact["UserID"]) == str(self.main.user["UserID"]):
            QMessageBox.warning(self, "提示", "不能给自己发送消息")
            return

        # 添加逻辑：如果用户试图输入"**"开头"**"结尾的文字，则自动删除开头和结尾的**
        if content.startswith("**") and content.endswith("**") and len(content) > 4:
            content = content[2:-2]

        self.client.send({
            "type": "send_message",
            "sender_id": self.main.user["UserID"],
            "receiver_id": self.current_contact["UserID"],
            "content": content
        }, callback=self._on_message_sent)

    def _on_message_sent(self, resp):
        if resp.get("success"):
            self.message_input.clear()
            self._load_messages()  # 重新加载消息
        else:
            QMessageBox.warning(self, "发送失败", resp.get("message"))

    def _refresh(self):
        # 页面刷新时重新加载联系人并重置聊天界面
        self._refresh_contacts()
        # 重置聊天界面
        self.current_contact = None
        self.message_display.clear()
        self.contact_info_label.setText("")
        self.online_status_label.hide()

    def _handle_real_time_message(self, resp):
        """处理实时消息"""
        if resp.get("type") == "real_time_message":
            message = resp.get("message", {})
            sender_id = message.get("sender_id")

            # 如果正在与发送方聊天，直接显示消息
            if self.current_contact and str(self.current_contact["UserID"]) == str(sender_id):
                self._display_new_message(message)
            else:
                # 否则更新未读消息计数
                sender_id_str = str(sender_id)
                self.unread_counts[sender_id_str] = self.unread_counts.get(sender_id_str, 0) + 1
                self.main._update_unread_count(1)  # 更新总未读数
                self._refresh_contact_list()  # 刷新联系人列表显示

    def _display_new_message(self, message):
        """显示新收到的消息"""
        # 格式化显示消息
        self._display_messages_append([message])
        # 滚动到底部
        self.message_display.moveCursor(QTextCursor.End)

    def _has_visible_messages_with_contact(self, contact_id):
        """
        检查与指定联系人是否有可见消息
        """
        if not self.main.user:
            return False

        # 发送请求检查是否有可见消息
        # 这里我们假设如果联系人出现在联系人列表中，就说明有可见消息
        # 或者我们可以简单地认为所有联系人都有可见消息
        # 在实际实现中，可能需要调用服务器接口检查消息可见性
        return True  # 简化处理，假设所有联系人都有可见消息

    def _add_contact(self):
        """添加联系人"""
        # 创建添加联系人对话框
        dialog = AddContactDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            contact_id = dialog.contact_id
            remark = dialog.remark

            # 检查是否试图添加自己为联系人
            if str(contact_id) == str(self.main.user["UserID"]):
                QMessageBox.warning(self, "提示", "不能添加自己为联系人")
                return

            # 发送添加联系人请求
            self.client.send({
                "type": "add_contact",
                "user_id": self.main.user["UserID"],
                "contact_id": contact_id,
                "remark": remark
            }, callback=self._on_contact_added)

    def _on_contact_added(self, resp):
        """处理添加联系人结果"""
        if resp.get("success"):
            QMessageBox.information(self, "成功", "联系人添加成功")
            # 自动发送一条消息
            contact = resp.get("contact")
            if contact and self.main.user:
                # 发送系统消息
                self.client.send({
                    "type": "send_message",
                    "sender_id": self.main.user["UserID"],
                    "receiver_id": contact["UserID"],
                    "content": f"我已添加您为联系人，现在可以开始聊天了"
                }, callback=lambda r: None)
            # 刷新联系人列表
            self._refresh_contacts()
        else:
            QMessageBox.warning(self, "失败", resp.get("message"))

    def showEvent(self, event):
        """页面显示时加载联系人"""
        super().showEvent(event)

    # 添加：更新联系人信息显示
    def _update_contact_info(self):
        """更新当前聊天对象信息显示"""
        if self.current_contact:
            user_id = self.current_contact.get("UserID", "未知")
            remark = self.current_contact.get("remark", "")
            username = self.current_contact.get("Username", "")
            nickname = self.current_contact.get("Nickname", "")

            # 构建显示文本
            # 使用统一的显示格式
            if remark and remark != nickname:
                info_text = f"与 {remark} ({nickname}) 聊天中 (ID: {user_id})"
            else:
                info_text = f"与 {nickname} 聊天中 (ID: {user_id})"

            self.contact_info_label.setText(info_text)
        else:
            self.contact_info_label.setText("")

    # 添加：修改备注功能
    def _edit_contact_remark(self):
        """修改当前选中联系人的备注"""
        current_item = self.contact_list.currentItem()
        if not current_item:
            QMessageBox.warning(self, "提示", "请先选择一个联系人")
            return

        contact = current_item.data(Qt.UserRole)
        current_remark = contact.get("remark", "")

        # 弹出对话框输入新备注
        new_remark, ok = QInputDialog.getText(self, "修改备注", "请输入新备注:", text=current_remark)
        if ok:
            self._update_contact_remark(contact["UserID"], new_remark)

    def _update_contact_remark(self, contact_id, remark):
        """更新联系人备注"""
        if not self.main.user:
            return

        self.client.send({
            "type": "update_contact_remark",
            "user_id": self.main.user["UserID"],
            "contact_id": contact_id,
            "remark": remark
        }, callback=self._on_contact_remark_updated)

    def _on_contact_remark_updated(self, resp):
        """处理更新联系人备注结果"""
        if resp.get("success"):
            QMessageBox.information(self, "成功", "备注更新成功")
            # 刷新联系人列表
            self._refresh_contacts()
        else:
            QMessageBox.warning(self, "失败", resp.get("message"))

    # 添加右键菜单
    def _show_contact_context_menu(self, position):
        """显示联系人右键菜单"""
        item = self.contact_list.itemAt(position)
        if not item:
            return

        contact = item.data(Qt.UserRole)

        menu = QMenu()
        view_profile_action = menu.addAction("查看资料")
        edit_remark_action = menu.addAction("修改备注")
        give_gift_action = menu.addAction("赠与")
        delete_contact_action = menu.addAction("删除联系人")

        action = menu.exec_(self.contact_list.mapToGlobal(position))

        if action == view_profile_action:
            self._view_contact_profile(contact)
        elif action == edit_remark_action:
            self._edit_contact_remark_context(contact)
        elif action == give_gift_action:
            self._give_gift_to_contact(contact)
        elif action == delete_contact_action:
            self._delete_contact(contact)

    def _view_contact_profile(self, contact):
        """查看联系人资料"""
        if not self.main.user:
            return

        self.client.send({
            "type": "get_user_profile",
            "user_id": self.main.user["UserID"],
            "target_id": contact["UserID"]
        }, callback=self._on_profile_received)

    def _on_profile_received(self, resp):
        """收到用户资料后的回调"""
        if resp.get("success"):
            user = resp["user"]

            # 权限组映射
            role_names = {
                1: "管理员",
                2: "VIP用户",
                3: "用户",
                4: "游客",
                5: "封禁"
            }

            # 获取权限组名称
            role_id = user.get('RoleID', 3)
            role_name = role_names.get(role_id, f"未知({role_id})")

            # 白名单状态
            white_state = '已通过' if user.get('WhiteState', 0) else '未通过'

            # QQ绑定状态
            qq_id = user.get('QQID') or '未绑定'

            # 格式化显示信息
            info_text = f"""
            <html>
            <head/>
            <body>
                <table border="0" style="margin: 10px; font-size: 14px;" cellspacing="5"> 
                    <tr>
                        <td><b>用户ID:</b></td>
                        <td>{user.get('UserID', '未知')}</td>
                    </tr>
                    <tr>
                        <td><b>用户名:</b></td>
                        <td>{user.get('Username', '未知')}</td>
                    </tr>
                    <tr>
                        <td><b>昵称:</b></td>
                        <td>{user.get('Nickname', '未知')}</td>
                    </tr>
                    <tr>
                        <td><b>邮箱:</b></td>
                        <td>{user.get('Email', '未知')}</td>
                    </tr>
                    <tr>
                        <td><b>手机:</b></td>
                        <td>{user.get('Phone', '未知')}</td>
                    </tr>
                    <tr>
                        <td><b>金币:</b></td>
                        <td style="color: #FFD700;">{user.get('Coins', 0)}</td>
                    </tr>
                    <tr>
                        <td><b>星星:</b></td>
                        <td style="color: #FFA500;">{user.get('Stars', 0)}</td>
                    </tr>
                    <tr>
                        <td><b>权限组:</b></td>
                        <td style="color: {'#FF0000' if role_id == 5 else '#00AA00' if role_id == 1 else '#0000FF'};">
                            {role_name}
                        </td>
                    </tr>
                    <tr>
                        <td><b>白名单:</b></td>
                        <td style="color: {'#00AA00' if user.get('WhiteState', 0) else '#FF0000'};">
                            {white_state}
                        </td>
                    </tr>
                    <tr>
                        <td><b>QQ绑定:</b></td>
                        <td>{qq_id}</td>
                    </tr>
                </table>
            </body>
            </html>
            """

            dialog = QDialog(self)
            dialog.setWindowTitle("用户资料")
            layout = QVBoxLayout(dialog)

            label = QLabel(info_text)
            label.setTextFormat(Qt.RichText)
            layout.addWidget(label)

            close_btn = QPushButton("关闭")
            close_btn.clicked.connect(dialog.close)
            layout.addWidget(close_btn)

            dialog.exec_()
        else:
            QMessageBox.warning(self, "获取资料失败", resp.get("message"))

    def _edit_contact_remark_context(self, contact):
        """通过右键菜单修改联系人备注"""
        current_remark = contact.get("remark", "")

        # 弹出对话框输入新备注
        new_remark, ok = QInputDialog.getText(self, "修改备注", "请输入新备注:", text=current_remark)
        if ok:
            self._update_contact_remark(contact["UserID"], new_remark)

    def _give_gift_to_contact(self, contact):
        """赠与礼物给联系人"""
        if not self.main.user:
            return

        # 获取赠与信息
        self.client.send({
            "type": "get_gift_info",
            "user_id": self.main.user["UserID"]
        }, callback=lambda resp: self._on_gift_info_received(resp, contact))

    def _on_gift_info_received(self, resp, contact):
        """收到赠与信息后的回调"""
        if resp.get("success"):
            self.gift_info = resp["gift_info"]
            self._show_gift_dialog(contact)
        else:
            QMessageBox.warning(self, "失败", f"获取赠与信息失败: {resp.get('message')}")

    def _show_gift_dialog(self, contact):
        """显示赠与对话框"""
        self.gift_dialog = QDialog(self)  # 保存对话框引用
        self.gift_dialog.setWindowTitle(f"赠与礼物给 {contact['Nickname']}")
        layout = QVBoxLayout(self.gift_dialog)

        # 金币赠与按钮
        coin_info = f"点击赠与1金币（今日赠与{self.gift_info['coins_given_today']}/{self.gift_info['coin_limit']}）"
        coin_btn = QPushButton(coin_info)
        coin_btn.setStyleSheet("""
            QPushButton {
                background-color: gold; 
                color: black; 
                font-weight: bold;
                padding: 10px;
                border-radius: 5px;
                border: 1px solid #d4af37;
            }
            QPushButton:hover {
                background-color: #ffd700;
            }
        """)
        coin_btn.clicked.connect(lambda: self._confirm_give_gift(contact, "coin"))

        # 星星赠与按钮
        star_info = f"点击赠与1星星（今日赠与{self.gift_info['stars_given_today']}/{self.gift_info['star_limit']}）"
        star_btn = QPushButton(star_info)
        star_btn.setStyleSheet("""
            QPushButton {
                background-color: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 1,
                                                  stop: 0 #0000ff, stop: 1 #00ffff);
                color: white;
                font-weight: bold;
                padding: 10px;
                border-radius: 5px;
                border: 1px solid #0000aa;
            }
            QPushButton:hover {
                background-color: qlineargradient(x1: 0, y1: 0, x2: 1, y2: 1,
                                                  stop: 0 #0000cc, stop: 1 #00cccc);
            }
        """)
        star_btn.clicked.connect(lambda: self._confirm_give_gift(contact, "star"))

        layout.addWidget(coin_btn)
        layout.addWidget(star_btn)

        close_btn = QPushButton("关闭")
        close_btn.setStyleSheet(""""
            QPushButton {
                background-color: #f0f0f0;
                color: #333;
                padding: 8px;
                border-radius: 5px;
                border: 1px solid #ccc;
            }
            QPushButton:hover {
                background-color: #e0e0e0;
            }
        """)
        close_btn.clicked.connect(self.gift_dialog.close)
        layout.addWidget(close_btn)

        self.gift_dialog.exec_()

    def _confirm_give_gift(self, contact, gift_type):
        """确认赠与礼物"""
        gift_name = "金币" if gift_type == "coin" else "星星"

        reply = QMessageBox.question(
            self,
            "确认赠与",
            f"确定要赠与 {contact['Nickname']} 1 {gift_name}吗？",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            self.client.send({
                "type": "give_gift",
                "sender_id": self.main.user["UserID"],
                "receiver_id": contact["UserID"],
                "gift_type": gift_type
            }, callback=self._on_gift_sent)

    def _on_gift_sent(self, resp):
        """礼物赠与后的回调"""
        if resp.get("success"):
            # 关闭赠与对话框
            if self.gift_dialog:
                self.gift_dialog.close()
                self.gift_dialog = None

            # 刷新消息显示
            self._load_messages()

            # 显示赠与消息
            QMessageBox.information(self, "成功", "赠与成功")

            # 自动刷新赠与信息
            self._refresh_gift_info()
        else:
            QMessageBox.warning(self, "失败", resp.get("message"))

    def _refresh_gift_info(self):
        """刷新赠与信息"""
        if not self.main.user:
            return

        self.client.send({
            "type": "get_gift_info",
            "user_id": self.main.user["UserID"]
        }, callback=self._on_gift_info_refreshed)

    def _on_gift_info_refreshed(self, resp):
        """赠与信息刷新后的回调"""
        if resp.get("success"):
            self.gift_info = resp["gift_info"]
            # 如果当前有赠与对话框打开，需要更新对话框内容
            # 这里我们简单地关闭当前对话框，用户可以重新打开查看最新信息
            if self.gift_dialog:
                self.gift_dialog.close()
                self.gift_dialog = None

    def _delete_contact(self, contact):
        """删除联系人"""
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除联系人 {contact['Nickname']} 吗？这将隐藏你们之间的聊天记录。",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            if not self.main.user:
                return

            self.client.send({
                "type": "delete_contact",
                "user_id": self.main.user["UserID"],
                "contact_id": contact["UserID"]
            }, callback=self._on_contact_deleted)

    def _on_contact_deleted(self, resp):
        """联系人删除后的回调"""
        if resp.get("success"):
            # 获取被删除的联系人ID
            if self.current_contact:
                contact_id = self.current_contact["UserID"]

                # 从内存中的联系人列表中移除
                self.contacts = [contact for contact in self.contacts if contact["UserID"] != contact_id]

                # 重新加载联系人列表
                self._refresh_contact_list()

                # 清空聊天区域和当前联系人
                self.current_contact = None
                self.message_display.clear()
                self.contact_info_label.setText("")
                self.online_status_label.hide()

            QMessageBox.information(self, "成功", "联系人已删除")
            self._refresh_contact_list()
        else:
            QMessageBox.warning(self, "失败", f"删除联系人失败: {resp.get('message')}")

    def _bind_signal(self):
        """绑定信号"""
        # 绑定客户端的实时消息信号到处理方法
        self.client.real_time_message.connect(self._handle_real_time_message)


# ========================= 添加联系人对话框 =========================
class AddContactDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("添加联系人")
        self.contact_id = None
        self.remark = ""
        self.parent = parent  # 保存父级引用
        self._init_ui()

    def _init_ui(self):
        layout = QFormLayout(self)

        self.id_edit = QLineEdit()
        self.remark_edit = QLineEdit()

        layout.addRow("用户ID:", self.id_edit)
        layout.addRow("备注名:", self.remark_edit)

        buttons = QHBoxLayout()
        ok_btn = QPushButton("确定")
        cancel_btn = QPushButton("取消")

        ok_btn.clicked.connect(self._accept)
        cancel_btn.clicked.connect(self.reject)

        buttons.addWidget(ok_btn)
        buttons.addWidget(cancel_btn)

        layout.addRow(buttons)

    def _accept(self):
        contact_id = self.id_edit.text().strip()
        self.remark = self.remark_edit.text().strip()

        if not contact_id:
            QMessageBox.warning(self, "提示", "请输入用户ID")
            return

        if not contact_id.isdigit():
            QMessageBox.warning(self, "提示", "用户ID必须是数字")
            return

        self.contact_id = int(contact_id)
        self.accept()


# ========================= 服务器控制台页面 =========================
class ServerConsolePage(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 服务器状态区域
        status_group = QGroupBox("服务器状态")
        status_layout = QVBoxLayout()

        self.server_status_label = QLabel("正在检查服务器状态...")
        self.online_count_label = QLabel("在线人数: 0")
        self.game_online_count_label = QLabel("游戏内在线: 0")

        status_layout.addWidget(self.server_status_label)
        status_layout.addWidget(self.online_count_label)
        status_layout.addWidget(self.game_online_count_label)
        status_group.setLayout(status_layout)

        # 命令执行区域
        command_group = QGroupBox("命令执行")
        command_layout = QVBoxLayout()

        self.command_input = QLineEdit()
        self.command_input.setPlaceholderText("输入Minecraft命令...")
        self.command_input.returnPressed.connect(self.execute_command)

        execute_btn = QPushButton("执行命令")
        execute_btn.clicked.connect(self.execute_command)

        self.command_output = QTextEdit()
        self.command_output.setReadOnly(True)

        command_layout.addWidget(QLabel("命令:"))
        command_layout.addWidget(self.command_input)
        command_layout.addWidget(execute_btn)
        command_layout.addWidget(QLabel("输出:"))
        command_layout.addWidget(self.command_output)
        command_group.setLayout(command_layout)

        # 在线玩家管理区域
        players_group = QGroupBox("在线玩家管理")
        players_layout = QVBoxLayout()

        self.players_table = QTableWidget(0, 4)
        self.players_table.setHorizontalHeaderLabels(["玩家名", "用户ID", "用户名", "操作"])
        self.players_table.horizontalHeader().setStretchLastSection(True)

        refresh_btn = QPushButton("刷新在线玩家")
        refresh_btn.clicked.connect(self.refresh_online_players)

        players_layout.addWidget(self.players_table)
        players_layout.addWidget(refresh_btn)
        players_group.setLayout(players_layout)

        # 添加到主布局
        layout.addWidget(status_group)
        layout.addWidget(command_group)
        layout.addWidget(players_group)

        # 定时刷新服务器状态
        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self.refresh_status)
        self.refresh_timer.start(30000)  # 每30秒刷新一次

        # 初始刷新
        self.refresh_status()

    def refresh_status(self):
        """刷新服务器状态"""
        if self.main.user:
            # 获取服务器状态
            self.main.client.send({
                "type": "get_server_status"
            }, lambda resp: self._update_server_status(resp))

            # 获取游戏内在线用户
            self.main.client.send({
                "type": "get_game_online_users",
                "user_id": self.main.user['UserID']
            }, lambda resp: self._update_game_online_status(resp))

    def _update_server_status(self, resp):
        """更新服务器状态显示"""
        if resp.get("success"):
            mc_online = resp.get("mc_server_online", False)
            online_count = resp.get("online_count", 0)

            if mc_online:
                self.server_status_label.setText("✅ Minecraft服务器在线")
                self.server_status_label.setStyleSheet("color: green; font-weight: bold;")
            else:
                self.server_status_label.setText("❌ Minecraft服务器离线")
                self.server_status_label.setStyleSheet("color: red; font-weight: bold;")

            self.online_count_label.setText(f"在线人数: {online_count}")

    def _update_game_online_status(self, resp):
        """更新游戏内在线状态"""
        if resp.get("success"):
            game_online_users = resp.get("game_online_users", [])
            self.game_online_count_label.setText(f"游戏内在线: {len(game_online_users)}")

            # 更新在线玩家表格
            self.players_table.setRowCount(len(game_online_users))
            for i, user_info in enumerate(game_online_users):
                self.players_table.setItem(i, 0, QTableWidgetItem(user_info.get("player_name", "")))
                self.players_table.setItem(i, 1, QTableWidgetItem(str(user_info.get("user_id", ""))))
                self.players_table.setItem(i, 2, QTableWidgetItem(user_info.get("username", "")))

                # 添加踢出按钮
                kick_btn = QPushButton("踢出")
                kick_btn.clicked.connect(lambda checked, name=user_info.get("player_name", ""): self.kick_player(name))
                self.players_table.setCellWidget(i, 3, kick_btn)

    def execute_command(self):
        """执行Minecraft命令"""
        command = self.command_input.text().strip()
        if not command or not self.main.user:
            return

        self.main.client.send({
            "type": "execute_mc_command",
            "user_id": self.main.user['UserID'],
            "command": command
        }, lambda resp: self._on_command_result(resp))

        self.command_input.clear()

    def _on_command_result(self, resp):
        """处理命令执行结果"""
        if resp.get("success"):
            result = resp.get("result", "命令执行成功")
            self.command_output.append(f">>> {result}")
        else:
            error_msg = resp.get("message", "命令执行失败")
            self.command_output.append(f"ERROR: {error_msg}")

    def kick_player(self, player_name):
        """踢出玩家"""
        if not player_name or not self.main.user:
            return

        reply = QMessageBox.question(self, "确认踢出", f"确定要踢出玩家 {player_name} 吗？")
        if reply == QMessageBox.Yes:
            self.main.client.send({
                "type": "kick_player",
                "user_id": self.main.user['UserID'],
                "player_name": player_name,
                "reason": "被管理员踢出"
            }, lambda resp: self._on_kick_result(resp, player_name))

    def _on_kick_result(self, resp, player_name):
        """处理踢出结果"""
        if resp.get("success"):
            QMessageBox.information(self, "成功", f"玩家 {player_name} 已被踢出")
            self.refresh_online_players()  # 刷新在线玩家列表
        else:
            error_msg = resp.get("message", "踢出失败")
            QMessageBox.warning(self, "失败", error_msg)

    def refresh_online_players(self):
        """刷新在线玩家"""
        if self.main.user:
            self.main.client.send({
                "type": "refresh_game_online_status",
                "user_id": self.main.user['UserID']
            }, lambda resp: self.refresh_status())


# ========================= 启动 =========================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())