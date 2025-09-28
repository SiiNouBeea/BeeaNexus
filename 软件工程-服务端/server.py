#!/usr/bin/env python3
"""
Desktop 后端服务
等价于 app.py 的所有路由，只是通过 TCP-JSON 暴应。
启动后监听 8001，客户端发送：
{"type":"login","username":"xxx","password":"xxx","seq":123}
即可收到对应 JSON 响应。
"""
import struct
import socketserver, json, threading, traceback
from tools import DatabaseManager
import datetime, decimal
import os
import shutil

db = DatabaseManager()

# 添加客户端连接管理
class ClientConnectionManager:
    def __init__(self):
        # 存储用户ID与连接的映射关系
        self.connections = {}
        self.lock = threading.Lock()
    
    def add_connection(self, user_id, connection):
        """添加用户连接"""
        with self.lock:
            self.connections[int(user_id)] = connection
            print(f"[S] 用户 {user_id} 连接已添加，当前连接数: {len(self.connections)} \n**当前链接映射：{self.connections}")
    
    def remove_connection(self, user_id):
        """移除用户连接"""
        with self.lock:
            if int(user_id) in self.connections:
                del self.connections[int(user_id)]
                print(f"[S] 用户 {user_id} 连接已移除，当前连接数: {len(self.connections)}")
    
    def get_connection(self, user_id):
        """获取用户连接"""
        with self.lock:
            return self.connections.get(int(user_id))
    
    def send_to_user(self, user_id, message):
        """向指定用户发送消息"""
        connection = self.get_connection(user_id)
        if connection:
            try:
                # 发送实时消息给客户端
                packed_message = TCPHandler._pack(message)
                connection.sendall(packed_message)
                print(f"[S] 实时消息已发送给用户 {user_id}")
                return True
            except Exception as e:
                print(f"[E] 发送消息给用户 {user_id} 失败: {e}")
                # 移除失效连接
                self.remove_connection(user_id)
                return False
        else:
            print(f"[W] 用户 {user_id} 不在线，无法发送实时消息")
            return False
    
    def get_online_users(self):
        """获取在线用户列表"""
        with self.lock:
            return list(self.connections.keys())

# 创建全局连接管理器实例
connection_manager = ClientConnectionManager()


# --------------------------------------------------
# 业务路由表
# --------------------------------------------------
def route_register(data):
    for k in ("username", "password", "nickname", "email", "phone", "playername"):
        if not data.get(k):
            return {"success": False, "message": f"缺少字段 {k}"}
    res = db.register_user(data["username"], data["password"], data["nickname"],
                           data["email"], data["phone"], data["playername"])
    return {"success": res is True, "message": res if res is not True else "注册成功"}


def route_login(data):
    u, p = data.get("username"), data.get("password")
    if not u or not p:
        return {"success": False, "message": "用户名或密码为空"}
    user = db.check_login(u, p)
    if not user:
        return {"success": False, "message": "用户名或密码错误"}
    
    # 检查用户是否已经在线（防止重复登录）
    if db.is_user_online(user["UserID"]):
        return {"success": False, "message": "该用户已在其他地方登录，无法重复登录"}
    
    user['RoleID'] = db.get_role_by_uid(user['UserID'])  # 添加角色ID信息
    # 获取客户端IP地址
    client_ip = data.get("client_ip", "")
    
    # 根据IP地址获取地理位置
    address = get_location_by_ip(client_ip)
    
    db.log_login(user["UserID"], client_ip, address)
    # 用户登录时标记为在线，并更新最后在线时间
    db.user_online(user["UserID"])
    # 更新用户的最后在线时间
    # 修复：使用datetime模块获取当前时间，而不是调用不存在的_get_now函数
    import datetime
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    db._execute("UPDATE Users SET last_online = %s WHERE UserID = %s", (now, user["UserID"]))
    
    # 打印当前在线用户
    online_users = db.get_online_users()
    print(f"[S] 用户 {user['Username']} (ID: {user['UserID']}) 上线，当前在线用户: {online_users}")
    
    # 获取未读消息信息
    unread_count = db.get_unread_messages_count(user["UserID"])
    unread_details = {str(item['sender_id']): item['unread_count'] 
                      for item in db.get_unread_messages_by_contact(user["UserID"])}
    
    return {"success": True, "user": user, "online_users": online_users, 
            "unread_count": unread_count, "unread_details": unread_details}


def get_location_by_ip(ip):
    """
    根据IP地址获取地理位置信息
    """
    if not ip or ip == "127.0.0.1":
        return "本地地址"
    
    try:
        import requests
        # 使用ip-api.com免费接口获取地理位置
        print(f"正在解析IP地址：{ip}", end=' ')
        response = requests.get(f"http://ip-api.com/json/{ip}?lang=zh-CN", timeout=5)
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "success":
                region = data.get("regionName", "")
                city = data.get("city", "")
                address = f"{region} {city}"
                print(f" ———— 获取位置成功: {address}")
                # 限制地址长度不超过255字符，防止数据库插入错误
                return address
        return "未知地址"
    except Exception as e:
        print(f"获取IP地理位置失败: {e}")
        return "未知地址"


def route_update_role(data):
    uid, rid = data.get("user_id"), data.get("role_id")
    if uid is None or rid is None:
        return {"success": False, "message": "缺少 user_id 或 role_id"}
    db.update_user_role(uid, rid)
    return {"success": True, "message": "权限更新成功"}


def route_add_whitelist(data):
    uid = data.get("user_id")
    if not uid:
        return {"success": False, "message": "缺少 user_id"}
    res = db.add_to_whitelist(uid)
    if res is True:
        return {"success": True, "message": "白名单添加成功"}
    return {"success": False, "message": res}


def route_sign(data):
    uid = data.get("user_id")
    if not uid:
        return {"success": False, "message": "缺少 user_id"}
    if db.has_sign_today(uid):
        return {"success": False, "message": "今日已签到"}
    ret = db.do_sign(uid)
    return {"success": True, "reward": ret}


def route_leaderboard(_):
    return {"success": True,
            "coin": db.coin_leaderboard(),
            "star": db.star_leaderboard()}


def route_profile(data):
    uid = data.get("user_id")
    if not uid:
        return {"success": False, "message": "缺少 user_id"}
    user = db.get_user_by_id(uid)
    if not user:
        return {"success": False, "message": "用户不存在"}
    user["RoleID"] = db.get_role_by_uid(uid)
    user["WhiteState"] = db.get_whitelist_state(uid) or 0  # 修复：处理None值
    # 修改QQID获取方式，避免数据库连接问题
    try:
        user["QQID"] = db.get_qq_by_uid(uid)
    except:
        user["QQID"] = None
    return {"success": True, "user": user}


def route_update_profile(data):
    """更新用户个人资料"""
    uid = data.get("user_id")
    nickname = data.get("nickname")
    email = data.get("email")
    phone = data.get("phone")
    password = data.get("password")

    if not uid:
        return {"success": False, "message": "缺少 user_id"}

    try:
        # 更新用户基本资料
        db.update_user_profile(uid, nickname, email, phone, password)

        # 更新用户个人资料
        first_name = data.get("first_name")
        last_name = data.get("last_name")
        gender = data.get("gender")
        birthday = data.get("birthday")
        bio = data.get("bio")

        db.update_user_personal_info(uid, first_name, last_name, gender, birthday, bio)
        return {"success": True, "message": "资料更新成功"}
    except Exception as e:
        return {"success": False, "message": f"更新失败: {str(e)}"}


def route_bind_qq(data):
    """绑定QQ号"""
    uid = data.get("user_id")
    qq = data.get("qq")

    if not uid:
        return {"success": False, "message": "缺少 user_id"}

    if not qq:
        return {"success": False, "message": "缺少 qq"}

    try:
        db.bind_qq(uid, qq)
        return {"success": True, "message": "QQ绑定成功"}
    except Exception as e:
        return {"success": False, "message": f"绑定失败: {str(e)}"}


def route_get_all_users(data):
    """获取所有用户信息"""
    try:
        users = db.get_all_users()
        # 添加在线状态信息到每个用户
        for user in users:
            user["online"] = db.is_user_online(user["UserID"])
        return {"success": True, "data": users, "online_users": db.get_online_users()}
    except Exception as e:
        return {"success": False, "message": f"获取用户信息失败: {str(e)}"}


def route_whitelist_apply(data):
    """提交白名单申请"""
    uid = data.get("user_id")
    playername = data.get("playername")
    genuine = data.get("genuine")
    reason = data.get("reason")

    if not uid:
        return {"success": False, "message": "缺少 user_id"}

    if not playername or genuine is None or not reason:
        return {"success": False, "message": "请填写完整信息"}

    try:
        # 检查用户是否已经通过白名单审核
        whitelist_state = db.get_whitelist_state(uid)
        if whitelist_state == 1:
            return {"success": False, "message": "您已通过白名单审核，无需再次申请"}

        # 检查是否已有未审核的申请 (修复：使用正确的函数)
        user_applications = db.get_whitelist_applications(uid)
        # 检查是否有待审核的申请
        pending_application = any(app.get("status") == "待审核" for app in user_applications)
        if pending_application:
            return {"success": False, "message": "您有未审核的申请，请等待审核完成后再申请"}

        # 检查今天申请次数是否超过限制（一天最多3次）
        import os
        from datetime import datetime
        today_date = datetime.now().strftime("%Y-%m-%d")
        today_count = 0

        base_path = "白名单相关/白名单申请"
        if os.path.exists(base_path):
            date_path = os.path.join(base_path, today_date)
            if os.path.exists(date_path):
                for filename in os.listdir(date_path):
                    if filename.startswith(f"{uid}-") and filename.endswith(".txt"):
                        today_count += 1

        # 同时检查已审核的申请
        approved_path = "白名单相关/已审核白名单"
        if os.path.exists(approved_path):
            for filename in os.listdir(approved_path):
                if filename.endswith(".txt"):
                    parts = filename.split("#")
                    if len(parts) >= 3:
                        file_uid = parts[1].split("-")[0]
                        file_date = parts[0]
                        if file_uid == str(uid) and file_date == today_date:
                            today_count += 1

        if today_count >= 3:
            return {"success": False, "message": "您今天已达到申请次数上限（3次）"}

        # 创建申请记录目录
        os.makedirs(f"白名单相关/白名单申请/{today_date}", exist_ok=True)

        # 生成申请记录
        genuine_text = "正版" if genuine == 1 else "离线"
        application_content = f"申请人ID: {uid}:{playername}\n游玩方式：{genuine_text}\n申请介绍：{reason}\n"

        # 保存申请记录到文件
        filename = f"白名单相关/白名单申请/{today_date}/{uid}-{playername}.txt"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(application_content)

        return {"success": True, "message": "申请已提交"}
    except Exception as e:
        return {"success": False, "message": f"提交失败: {str(e)}"}


def route_get_user_whitelist_applications(data):
    """获取用户白名单申请记录"""
    uid = data.get("user_id")

    if not uid:
        return {"success": False, "message": "缺少 user_id"}

    try:
        import os
        from datetime import datetime

        applications = []

        # 检查申请记录目录是否存在
        base_path = "白名单相关/白名单申请"
        if os.path.exists(base_path):
            # 遍历所有日期目录
            for date_dir in os.listdir(base_path):
                date_path = os.path.join(base_path, date_dir)
                if os.path.isdir(date_path):
                    # 遍历该日期下的所有申请文件
                    for filename in os.listdir(date_path):
                        if filename.startswith(f"{uid}-") and filename.endswith(".txt"):
                            filepath = os.path.join(date_path, filename)
                            try:
                                with open(filepath, "r", encoding="utf-8") as f:
                                    content = f.read()
                                    # 从文件名解析玩家名
                                    playername = filename[len(f"{uid}-"):-4]  # 去掉uid-前缀和.txt后缀
                                    applications.append({
                                        "date": date_dir,
                                        "playername": playername,
                                        "status": "待审核",
                                        "content": content
                                    })
                            except Exception:
                                continue

        # 同样检查已审核的申请
        approved_path = "白名单相关/已审核白名单"
        if os.path.exists(approved_path):
            for filename in os.listdir(approved_path):
                if filename.endswith(".txt"):
                    # 解析文件名获取用户ID
                    parts = filename.split("#")
                    if len(parts) >= 3:
                        file_uid = parts[1].split("-")[0]  # 从"用户ID-玩家名"中提取用户ID
                        if file_uid == str(uid):
                            try:
                                # 从文件名解析信息
                                date = parts[0]
                                user_player = parts[1].split("-")
                                user_id = user_player[0]
                                playername = user_player[1] if len(user_player) > 1 else ""
                                state = parts[2][:-4]  # 去掉.txt后缀

                                applications.append({
                                    "date": date,
                                    "playername": playername,
                                    "status": state,
                                    "content": f"申请已{state}"
                                })
                            except Exception:
                                continue

        # 按申请时间排序（最新的在前）
        applications.sort(key=lambda x: x["date"], reverse=True)

        return {"success": True, "applications": applications}
    except Exception as e:
        return {"success": False, "message": f"获取申请记录失败: {str(e)}"}


def route_get_all_whitelist_applications(data):
    """获取所有白名单申请"""
    try:
        import os

        applications = []

        # 检查申请记录目录是否存在
        base_path = "白名单相关/白名单申请"
        if os.path.exists(base_path):
            # 遍历所有日期目录
            for date_dir in os.listdir(base_path):
                date_path = os.path.join(base_path, date_dir)
                if os.path.isdir(date_path):
                    # 遍历该日期下的所有申请文件
                    for filename in os.listdir(date_path):
                        if filename.endswith(".txt"):
                            filepath = os.path.join(date_path, filename)
                            try:
                                with open(filepath, "r", encoding="utf-8") as f:
                                    content = f.read()
                                    # 从文件名解析用户ID和玩家名
                                    uid_and_player = filename[:-4]  # 去掉.txt后缀
                                    uid, playername = uid_and_player.split("-", 1)

                                    applications.append({
                                        "date": date_dir,
                                        "user_id": uid,
                                        "playername": playername,
                                        "status": "待审核",
                                        "content": content
                                    })
                            except Exception:
                                continue

        # 按申请时间排序（最新的在前）
        applications.sort(key=lambda x: x["date"], reverse=True)

        return {"success": True, "applications": applications}
    except Exception as e:
        return {"success": False, "message": f"获取申请列表失败: {str(e)}"}


def route_process_whitelist_application(data):
    """处理白名单申请"""
    date = data.get("date")
    user_id = data.get("user_id")
    approved = data.get("approved")
    playername = data.get("playername")
    genuine = data.get("genuine")

    print(f"[S] 收到白名单审核请求 -- {date}: UID:{user_id} 申请ID:{playername} 审核结果：{approved}")

    if not date or not user_id or approved is None:
        return {"success": False, "message": "缺少必要参数"}

    try:
        import os
        import shutil
        from datetime import datetime

        # 构建原始文件路径和目标文件路径
        original_path = f"白名单相关/白名单申请/{date}/{user_id}-{playername}.txt"
        state_str = "已通过" if approved else "未通过"
        target_path = f"白名单相关/已审核白名单/{date}#{user_id}-{playername}#{state_str}.txt"
        # 确保目标目录存在
        os.makedirs(os.path.dirname(target_path), exist_ok=True)

        # 检查原始文件是否存在
        file_exists = os.path.exists(original_path)

        # 移动文件（如果存在）
        if file_exists:
            shutil.move(original_path, target_path)

        # 根据审核结果更新数据库
        if approved:
            '''
            # 使用RCON命令添加白名单
            try:
                from mcrcon import MCRcon
                from tools import RCON_CONFIG
                with MCRcon(RCON_CONFIG['host'], RCON_CONFIG['password'], RCON_CONFIG['port']) as rcon:
                    rcon.command(f"whitelist add {playername}")
            except Exception as e:
                print(f"添加白名单失败: {e}")
            '''

            # 更新数据库中的白名单状态
            db._execute("UPDATE PlayerData SET WhiteState=1, PassDate=%s, Genuine=%s WHERE UserID=%s",
                        (datetime.now().strftime('%Y-%m-%d'), genuine, user_id))
        else:
            # 如果申请被拒绝，确保白名单状态为0
            db._execute("UPDATE PlayerData SET WhiteState=0 WHERE UserID=%s", (user_id,))

        action = "通过" if approved else "拒绝"
        return {"success": True, "message": f"申请已{action}"}
    except Exception as e:
        return {"success": False, "message": f"处理失败: {str(e)}"}


def route_get_users_count(data):
    """获取用户总数"""
    try:
        count = db.get_users_count()
        return {"success": True, "count": count}
    except Exception as e:
        return {"success": False, "message": f"获取用户数量失败: {str(e)}"}


def route_get_users_by_page(data):
    """分页获取用户数据"""
    try:
        page = data.get("page", 1)
        page_size = data.get("page_size", 10)
        users = db.get_users_by_page(page, page_size)
        # 添加在线状态信息到每个用户
        for user in users:
            user["online"] = db.is_user_online(user["UserID"])
        return {"success": True, "data": users, "online_users": db.get_online_users()}
    except Exception as e:
        return {"success": False, "message": f"获取用户数据失败: {str(e)}"}


# ---------- 通信系统路由 ----------
def route_get_contacts(data):
    """获取联系人列表"""
    user_id = data.get("user_id")
    if not user_id:
        return {"success": False, "message": "缺少 user_id"}

    try:
        contacts = db.get_user_contacts(user_id)
        # 获取用户备注信息
        remarks = db.get_user_contact_remarks(user_id)

        # 合并备注信息到联系人列表
        for contact in contacts:
            contact_id = contact["UserID"]
            remark_info = remarks.get(str(contact_id), {})
            contact["remark"] = remark_info.get("remark", "")
            # 添加在线状态信息
            contact["online"] = db.is_user_online(contact_id)

        return {"success": True, "contacts": contacts, "online_users": db.get_online_users()}
    except Exception as e:
        return {"success": False, "message": f"获取联系人失败: {str(e)}"}


def route_get_messages(data):
    """获取两个用户之间的消息"""
    user_id = data.get("user_id")
    contact_id = data.get("contact_id")

    if not user_id or not contact_id:
        return {"success": False, "message": "缺少必要参数"}

    try:
        messages = db.get_messages_between_users(user_id, contact_id)
        return {"success": True, "messages": messages}
    except Exception as e:
        return {"success": False, "message": f"获取消息失败: {str(e)}"}


def route_send_message(data):
    """发送消息"""
    sender_id = data.get("sender_id")
    receiver_id = data.get("receiver_id")
    content = data.get("content")

    if not sender_id or not receiver_id or not content:
        return {"success": False, "message": "缺少必要参数"}

    try:
        db.send_message(sender_id, receiver_id, content)
        
        # 检查接收者是否在线
        if db.is_user_online(receiver_id):
            # 构造实时消息
            import datetime
            message_data = {
                "sender_id": sender_id,
                "receiver_id": receiver_id,
                "content": content,
                "timestamp": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            
            # 发送实时消息给在线接收者
            response = {
                "type": "real_time_message",
                "message": message_data
            }
            
            # 使用连接管理器发送实时消息
            connection_manager.send_to_user(receiver_id, response)
            
        return {"success": True, "message": "消息发送成功"}
    except Exception as e:
        return {"success": False, "message": f"发送消息失败: {str(e)}"}


def route_update_contact_remark(data):
    """更新联系人备注"""
    user_id = data.get("user_id")
    contact_id = data.get("contact_id")
    remark = data.get("remark", "")

    if not user_id or not contact_id:
        return {"success": False, "message": "缺少必要参数"}

    try:
        db.update_contact_remark(user_id, contact_id, remark)
        return {"success": True, "message": "备注更新成功"}
    except Exception as e:
        return {"success": False, "message": f"更新备注失败: {str(e)}"}


def route_get_user_profile(data):
    """获取用户资料"""
    user_id = data.get("user_id")
    target_id = data.get("target_id")

    if not user_id or not target_id:
        return {"success": False, "message": "缺少必要参数"}

    try:
        user = db.get_user_by_id(target_id)
        if not user:
            return {"success": False, "message": "用户不存在"}

        user["RoleID"] = db.get_role_by_uid(target_id)
        user["WhiteState"] = db.get_whitelist_state(target_id) or 0
        try:
            user["QQID"] = db.get_qq_by_uid(target_id)
        except:
            user["QQID"] = None

        return {"success": True, "user": user}
    except Exception as e:
        return {"success": False, "message": f"获取用户资料失败: {str(e)}"}


def route_give_gift(data):
    """赠与礼物"""
    sender_id = data.get("sender_id")
    receiver_id = data.get("receiver_id")
    gift_type = data.get("gift_type")  # "coin" 或 "star"

    if not sender_id or not receiver_id or not gift_type:
        return {"success": False, "message": "缺少必要参数"}

    if gift_type not in ["coin", "star"]:
        return {"success": False, "message": "无效的礼物类型"}

    try:
        result = db.give_gift(sender_id, receiver_id, gift_type)
        if result["success"]:
            # 记录赠与消息
            if gift_type == "coin":
                message = f"**<span style='color: #C29E4A;'>{result['sender_name']} 对 {result['receiver_name']} 赠与了 {result['amount']} 金币</span>**"
            else:  # star
                message = f"**<span style='color: blue;'>{result['sender_name']} 对 {result['receiver_name']} 赠与了 {result['amount']} 星星</span>**"

            db.send_message(sender_id, receiver_id, message)
            result["message"] = message
        return result
    except Exception as e:
        return {"success": False, "message": f"赠与失败: {str(e)}"}


def route_get_gift_info(data):
    """获取赠与信息"""
    user_id = data.get("user_id")

    if not user_id:
        return {"success": False, "message": "缺少 user_id"}

    try:
        gift_info = db.get_user_gift_info(user_id)
        return {"success": True, "gift_info": gift_info}
    except Exception as e:
        return {"success": False, "message": f"获取赠与信息失败: {str(e)}"}


def route_delete_contact(data):
    """删除联系人"""
    user_id = data.get("user_id")
    contact_id = data.get("contact_id")

    if not user_id or not contact_id:
        return {"success": False, "message": "缺少必要参数"}

    try:
        db.delete_contact(user_id, contact_id)
        return {"success": True, "message": "联系人删除成功"}
    except Exception as e:
        return {"success": False, "message": f"删除联系人失败: {str(e)}"}


def route_add_contact(data):
    """添加联系人"""
    user_id = data.get("user_id")
    contact_id = data.get("contact_id")
    remark = data.get("remark", "")

    if not user_id or not contact_id:
        return {"success": False, "message": "缺少必要参数"}

    try:
        # 检查联系人是否存在
        contact = db.get_user_by_id(contact_id)
        if not contact:
            return {"success": False, "message": "联系人不存在"}

        # 添加联系人（通过发送一条系统消息实现）
        db.add_contact(user_id, contact_id)

        # 如果有备注，则保存备注
        if remark:
            db.update_contact_remark(user_id, contact_id, remark)

        return {"success": True, "message": "联系人添加成功", "contact": contact}
    except Exception as e:
        return {"success": False, "message": f"添加联系人失败: {str(e)}"}


def route_user_online(data):
    """用户上线"""
    user_id = data.get("user_id")
    if not user_id:
        return {"success": False, "message": "缺少 user_id"}

    try:
        db.user_online(user_id)
        # 打印当前在线用户
        online_users = db.get_online_users()
        # 获取用户名
        user = db.get_user_by_id(user_id)
        username = user.get('Username', '未知用户') if user else '未知用户'
        print(f"[S] 用户 {username} (ID: {user_id}) 上线，当前在线用户: {online_users}")
        
        # 返回当前在线用户列表
        return {"success": True, "message": "在线状态已更新", "online_users": online_users}
    except Exception as e:
        return {"success": False, "message": f"更新在线状态失败: {str(e)}"}


# 添加用户下线路由
def route_user_offline(data):
    """用户下线"""
    user_id = data.get("user_id")
    if not user_id:
        return {"success": False, "message": "缺少 user_id"}

    try:
        db.user_offline(user_id)
        # 打印当前在线用户
        online_users = db.get_online_users()
        # 获取用户名
        user = db.get_user_by_id(user_id)
        username = user.get('Username', '未知用户') if user else '未知用户'
        print(f"[S] 用户 {username} (ID: {user_id}) 下线，当前在线用户: {online_users}")
        
        # 返回当前在线用户列表
        return {"success": True, "message": "离线状态已更新", "online_users": online_users}
    except Exception as e:
        return {"success": False, "message": f"更新离线状态失败: {str(e)}"}


# 在路由表中添加新路由
def route_has_visible_messages(data):
    """检查用户与联系人之间是否有可见消息"""
    user_id = data.get("user_id")
    contact_id = data.get("contact_id")

    if not user_id or not contact_id:
        return {"success": False, "message": "缺少必要参数"}

    try:
        # 查询是否有可见消息
        query = """
        SELECT COUNT(*) as count FROM messages 
        WHERE ((sender_id = %s AND receiver_id = %s AND visible_to_sender = TRUE) 
           OR (sender_id = %s AND receiver_id = %s AND visible_to_receiver = TRUE))
        """
        result = db._fetchone(query, (user_id, contact_id, contact_id, user_id))
        
        has_visible = result['count'] > 0 if result else False
        return {"success": True, "has_visible_messages": has_visible}
    except Exception as e:
        return {"success": False, "message": f"检查消息可见性失败: {str(e)}"}

# 添加获取未读消息的路由
def route_get_unread_messages(data):
    """获取未读消息数"""
    user_id = data.get("user_id")
    if not user_id:
        return {"success": False, "message": "缺少 user_id"}

    try:
        unread_count = db.get_unread_messages_count(user_id)
        unread_details = {str(item['sender_id']): item['unread_count'] 
                          for item in db.get_unread_messages_by_contact(user_id)}
        return {"success": True, "unread_count": unread_count, "unread_details": unread_details}
    except Exception as e:
        return {"success": False, "message": f"获取未读消息失败: {str(e)}"}

# 添加标记消息为已读的路由
def route_mark_messages_as_read(data):
    """标记消息为已读"""
    user_id = data.get("user_id")
    contact_id = data.get("contact_id")
    
    if not user_id or not contact_id:
        return {"success": False, "message": "缺少必要参数"}
    
    try:
        db.mark_messages_as_read(user_id, contact_id)
        # 获取更新后的未读消息数
        unread_count = db.get_unread_messages_count(user_id)
        unread_details = {str(item['sender_id']): item['unread_count'] 
                          for item in db.get_unread_messages_by_contact(user_id)}
        return {"success": True, "unread_count": unread_count, "unread_details": unread_details}
    except Exception as e:
        return {"success": False, "message": f"标记消息为已读失败: {str(e)}"}

# 更新路由表，添加新路由
ROUTER = {
    "register": route_register,
    "login": route_login,
    "update_role": route_update_role,
    "add_to_whitelist": route_add_whitelist,
    "sign": route_sign,
    "leaderboard": route_leaderboard,
    "profile": route_profile,
    "update_profile": route_update_profile,
    "bind_qq": route_bind_qq,
    "get_all_users": route_get_all_users,
    "whitelist_apply": route_whitelist_apply,
    "get_user_whitelist_applications": route_get_user_whitelist_applications,
    "get_all_whitelist_applications": route_get_all_whitelist_applications,
    "process_whitelist_application": route_process_whitelist_application,
    # 添加新的路由
    "get_users_count": route_get_users_count,
    "get_users_by_page": route_get_users_by_page,
    # 通信系统路由
    "get_contacts": route_get_contacts,
    "get_messages": route_get_messages,
    "send_message": route_send_message,
    "update_contact_remark": route_update_contact_remark,
    "get_user_profile": route_get_user_profile,
    "give_gift": route_give_gift,
    "get_gift_info": route_get_gift_info,
    "delete_contact": route_delete_contact,
    "add_contact": route_add_contact,
    "user_online": route_user_online,
    "user_offline": route_user_offline,
    "has_visible_messages": route_has_visible_messages,
    "get_unread_messages": route_get_unread_messages,  # 添加获取未读消息路由
    "mark_messages_as_read": route_mark_messages_as_read  # 添加标记消息为已读路由
}


class TCPHandler(socketserver.BaseRequestHandler):
    def __init__(self, request, client_address, server):
        # 保存客户端IP地址和当前用户ID的映射
        self.client_user_map = {}
        super().__init__(request, client_address, server)
        
    @staticmethod
    def _pack(msg: dict) -> bytes:
        def _default(o):
            if isinstance(o, (datetime.datetime, datetime.date)):
                return o.isoformat()
            if isinstance(o, decimal.Decimal):
                return float(o)
            if isinstance(o, bytes):
                return o.decode('utf-8')
            raise TypeError(f'Object of type {o.__class__.__name__} is not JSON serializable')

        body = json.dumps(msg, ensure_ascii=False, separators=(',', ':'), default=_default).encode('utf-8')
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

    def handle(self):
        conn = self.request
        client_ip = self.client_address[0]  # 获取客户端IP地址
        current_user_id = None  # 当前连接的用户ID

        try:
            while True:
                len_bs = self._recv_exact(conn, 4)
                body_len = struct.unpack('>I', len_bs)[0]
                body = self._recv_exact(conn, body_len)
                req = json.loads(body.decode('utf-8'))

                # 获取客户端发送的IP地址（如果有的话）
                client_sent_ip = req.get("client_ip", "未知")
                if client_sent_ip != "未知":
                    client_ip = client_sent_ip
                    
                # 记录用户ID与连接的关联
                if req.get("type") == "login" and req.get("user_id"):
                    current_user_id = req.get("user_id")
                    self.client_user_map[conn] = current_user_id
                    # 将连接添加到连接管理器
                    connection_manager.add_connection(current_user_id, conn)
                elif req.get("type") == "login" and req.get("username"):
                    # 从登录请求中获取用户信息
                    user = db.check_login(req.get("username"), req.get("password"))
                    if user:
                        current_user_id = user.get("UserID")
                        self.client_user_map[conn] = current_user_id
                        # 将连接添加到连接管理器
                        connection_manager.add_connection(current_user_id, conn)
                
                # 添加详细的日志信息
                timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                req_type = req.get("type", "unknown")
                user_info = f"UID:{req.get('user_id', 'N/A')}" if req.get("user_id") else "未登录用户"
                print(f"[{timestamp}] [客户端 {client_ip}] [请求: {req_type}] [用户: {user_info}] 收到请求: {req}")

                handler = ROUTER.get(req.get("type"))
                if not handler:
                    resp = {"success": False, "message": "未知请求类型"}
                else:
                    resp = handler(req)

                # 关键：把请求自带的 type & seq 原造带回
                resp["type"] = req.get("type")
                resp["seq"] = req.get("seq")
                conn.sendall(self._pack(resp))

                # 添加响应日志
                success_status = "成功" if resp.get("success", False) else "失败"
                message = resp.get("message", "")
                print(f"[{timestamp}] [客户端 {client_ip}] [请求: {req_type}] [状态: {success_status}] 响应: {message}")
        except (ConnectionResetError, BrokenPipeError):
            # 记录客户端断开连接
            timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            # 检查是否知道是哪个用户断开连接
            user_id = self.client_user_map.get(conn)
            if user_id:
                # 标记用户为离线
                db.user_offline(user_id)
                # 从连接管理器中移除连接
                connection_manager.remove_connection(user_id)
                # 获取用户名
                user = db.get_user_by_id(user_id)
                username = user.get('Username', '未知用户') if user else '未知用户'
                print(f"[{timestamp}] [客户端 {client_ip}] 用户 {username} (ID: {user_id}) 断开连接")
                
                # 打印当前在线用户
                online_users = db.get_online_users()
                print(f"[S] 当前在线用户: {online_users}")
            else:
                print(f"[{timestamp}] [客户端 {client_ip}] 未知用户断开连接")
            pass
        except Exception as e:
            traceback.print_exc()
        finally:
            # 清理连接相关的资源
            if conn in self.client_user_map:
                # 确保在finally块中也处理用户离线逻辑
                user_id = self.client_user_map.get(conn)
                if user_id:
                    db.user_offline(user_id)
                    # 从连接管理器中移除连接
                    connection_manager.remove_connection(user_id)
                    # 获取用户名
                    user = db.get_user_by_id(user_id)
                    username = user.get('Username', '未知用户') if user else '未知用户'
                    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    print(f"[{timestamp}] [客户端 {client_ip}] 用户 {username} (ID: {user_id}) 断开连接")
                    
                    # 打印当前在线用户
                    online_users = db.get_online_users()
                    print(f"[S] 当前在线用户: {online_users}")
                del self.client_user_map[conn]

# --------------------------------------------------
# 启动入口
# --------------------------------------------------
if __name__ == "__main__":
    HOST, PORT = "0.0.0.0", 8000  
    server = socketserver.ThreadingTCPServer((HOST, PORT), TCPHandler)
    print(f"[+] Desktop-Server 启动 @ {HOST}:{PORT}")
    server.serve_forever()