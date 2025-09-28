import sys
import mysql.connector
import bcrypt
import datetime
import uuid as _uuid
from mcrcon import MCRcon
import re  # 添加正则表达式模块用于格式验证

# ----------------------- 基础配置 -----------------------
DB_CONFIG = {
    'user': 'root',
    'password': 'ABuL1314',
    'host': '127.0.0.1',
    'database': 'User_All'
}

RCON_CONFIG = {
    'host': '127.0.0.1',
    'port': 25575,
    'password': 'IloveCzy'
}


# ----------------------- 工具函数 -----------------------
def _get_now():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _hash_pwd(plain):
    return bcrypt.hashpw(plain.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def _check_pwd(plain, hashed):
    return bcrypt.checkpw(plain.encode('utf-8'), hashed.encode('utf-8'))


def _rcon(cmd: str) -> str:
    """执行单条 RCON 命令并返回结果"""
    with MCRcon(RCON_CONFIG['host'], RCON_CONFIG['password'], RCON_CONFIG['port']) as r:
        return r.command(cmd)


# 添加邮箱格式验证函数
def _validate_email(email):
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None


# 添加电话号码格式验证函数
def _validate_phone(phone):
    # 支持中国大陆手机号码格式
    pattern = r'^1[3-9]\d{9}$'
    return re.match(pattern, phone) is not None


# ----------------------- 连接池 -----------------------
class DatabaseManager:
    def __init__(self, cfg=None):
        self.cfg = cfg or DB_CONFIG
        # 添加在线用户列表
        self.online_users = set()

    # ---------- 内部 ----------
    def _conn(self):
        return mysql.connector.connect(**self.cfg)

    def _fetchone(self, sql, params=None):
        with self._conn() as c:
            with c.cursor(dictionary=True) as cur:
                cur.execute(sql, params or ())
                return cur.fetchone()

    def _fetchall(self, sql, params=None):
        with self._conn() as c:
            with c.cursor(dictionary=True) as cur:
                cur.execute(sql, params or ())
                return cur.fetchall()

    def _execute(self, sql, params=None):
        with self._conn() as c:
            with c.cursor() as cur:
                cur.execute(sql, params or ())
                c.commit()
                return cur.lastrowid

    # ---------- 登录/注册 ----------
    def register_user(self, username, password, nickname, email, phone, playername):
        try:
            # 验证邮箱格式
            if not _validate_email(email):
                return "邮箱格式不正确"

            # 验证电话号码格式
            if not _validate_phone(phone):
                return "手机号格式不正确"

            # 检查用户名、邮箱和手机号是否已存在
            existing_user = self._fetchone(
                "SELECT Username, Email, Phone FROM Users WHERE Username = %s OR Email = %s OR Phone = %s",
                (username, email, phone)
            )

            if existing_user:
                # 返回具体的错误信息，仿照app.py的逻辑
                if existing_user['Username'] == username:
                    return "用户名已存在"
                elif existing_user['Email'] == email:
                    return "邮箱已被使用"
                elif existing_user['Phone'] == phone:
                    return "手机号已被使用"

            # 插入数据到数据库，仿照app.py的注册逻辑
            # 1. 创建新用户
            uid = self._execute(
                "INSERT INTO Users (Username, Password, Nickname, Email, Phone, CreatedAt) VALUES (%s,%s,%s,%s,%s,%s)",
                (username, _hash_pwd(password), nickname, email, phone, _get_now())
            )

            # 2. 在用户权限组中添加默认权限 (RoleID=3)
            self._execute("INSERT INTO UserRoles_Con (UserID, RoleID) VALUES (%s, 3)", (uid,))

            # 3. 创建用户档案信息
            import random
            gender_choices = ['武装直升机', '沃尔玛购物袋', '死亡花岗岩', '男', '女']
            self._execute(
                "INSERT INTO UserProfiles (UserID, FirstName, LastName, Birthday, Gender, Bio) VALUES (%s, %s, %s, %s, %s, %s)",
                (uid, 'New', 'User', '2024-1-01', random.choice(gender_choices), '没有简介')
            )

            # 4. 创建玩家数据
            # 生成UUID（仿照app.py中调用get_uuid的方式）
            import uuid
            player_uuid = str(uuid.uuid4()).replace('-', '')
            self._execute(
                "INSERT INTO PlayerData (UserID, RoleID, PlayerName, WhiteState, uuid) VALUES (%s, %s, %s, %s, %s)",
                (uid, 3, playername, 0, player_uuid)
            )

            return True
        except mysql.connector.Error as e:
            return str(e)

    def check_login(self, username, password):
        user = self._fetchone("SELECT * FROM Users WHERE Username=%s", (username,))
        if user and _check_pwd(password, user['Password']):
            user['CreatedAt'] = user['CreatedAt'].isoformat() if user['CreatedAt'] else None
            return user
        return None

    # ---------- 用户资料 ----------
    def update_user_profile(self, uid, nickname, email, phone, password=None):
        sql, params = "UPDATE Users SET Nickname=%s, Email=%s, Phone=%s", (nickname, email, phone)
        if password:
            sql += ", Password=%s"
            params += (_hash_pwd(password),)
        sql += " WHERE UserID=%s"
        params += (uid,)
        self._execute(sql, params)
        return True

    def update_user_personal_info(self, uid, first_name, last_name, gender, birthday, bio):
        """更新用户个人资料信息"""
        sql = """UPDATE UserProfiles 
                 SET FirstName=%s, LastName=%s, Gender=%s, Birthday=%s, Bio=%s 
                 WHERE UserID=%s"""
        params = (first_name, last_name, gender, birthday, bio, uid)
        self._execute(sql, params)
        return True

    def get_user_by_id(self, uid):
        user = self._fetchone("SELECT * FROM Users WHERE UserID=%s", (uid,))
        if user:
            # 获取用户个人资料
            profile = self._fetchone("SELECT * FROM UserProfiles WHERE UserID=%s", (uid,))
            if profile:
                user.update(profile)
        return user

    def get_all_users(self):
        # 修改查询语句，包含RoleID字段
        return self._fetchall("""
            SELECT u.*, 
                   ur.RoleID,
                   pd.PlayerName, 
                   pd.Genuine, 
                   pd.WhiteState, 
                   pd.PassDate, 
                   qq.QQID
            FROM Users u
            LEFT JOIN UserRoles_Con ur ON u.UserID = ur.UserID
            LEFT JOIN PlayerData pd ON u.UserID = pd.UserID
            LEFT JOIN UserQQ_Con qq ON u.UserID = qq.UserID
        """)

    def get_users_count(self):
        """获取用户总数"""
        result = self._fetchone("SELECT COUNT(*) as count FROM Users")
        return result['count'] if result else 0

    def get_users_by_page(self, page, page_size=10):
        """分页获取用户数据"""
        offset = (page - 1) * page_size
        return self._fetchall(f"""
            SELECT u.*, 
                   ur.RoleID,
                   pd.PlayerName, 
                   pd.Genuine, 
                   pd.WhiteState, 
                   pd.PassDate, 
                   qq.QQID
            FROM Users u
            LEFT JOIN UserRoles_Con ur ON u.UserID = ur.UserID
            LEFT JOIN PlayerData pd ON u.UserID = pd.UserID
            LEFT JOIN UserQQ_Con qq ON u.UserID = qq.UserID
            ORDER BY u.UserID
            LIMIT {page_size} OFFSET {offset}
        """)

    # ---------- 通信系统 ----------
    def get_user_contacts(self, user_id):
        """获取用户的联系人列表（与当前用户有过可见通信的用户）"""
        query = """
        SELECT DISTINCT u.UserID, u.Username, u.Nickname
        FROM Users u
        WHERE u.UserID IN (
            SELECT DISTINCT sender_id 
            FROM messages 
            WHERE receiver_id = %s AND visible_to_receiver = TRUE
            UNION
            SELECT DISTINCT receiver_id 
            FROM messages 
            WHERE sender_id = %s AND visible_to_sender = TRUE
        ) AND u.UserID != %s
        """
        return self._fetchall(query, (user_id, user_id, user_id))

    # 添加获取未读消息数的方法
    def get_unread_messages_count(self, user_id):
        """获取用户未读消息数"""
        query = """
        SELECT COUNT(*) as unread_count
        FROM messages 
        WHERE receiver_id = %s AND visible_to_receiver = TRUE AND is_read = FALSE
        """
        result = self._fetchone(query, (user_id,))
        return result['unread_count'] if result else 0

    # 添加获取与各联系人的未读消息数的方法
    def get_unread_messages_by_contact(self, user_id):
        """获取用户与各联系人的未读消息数"""
        query = """
        SELECT sender_id, COUNT(*) as unread_count
        FROM messages 
        WHERE receiver_id = %s AND visible_to_receiver = TRUE AND is_read = FALSE
        GROUP BY sender_id
        """
        return self._fetchall(query, (user_id,))

    # 添加标记消息为已读的方法
    def mark_messages_as_read(self, user_id, contact_id):
        """将用户与指定联系人之间的消息标记为已读"""
        query = """
        UPDATE messages 
        SET is_read = TRUE 
        WHERE receiver_id = %s AND sender_id = %s AND is_read = FALSE
        """
        self._execute(query, (user_id, contact_id))
        return True

    # 添加获取在线用户列表的方法
    def get_online_users(self):
        """获取当前在线用户列表"""
        return list(self.online_users)

    # 添加用户上线方法
    def user_online(self, user_id):
        """标记用户为在线状态"""
        self.online_users.add(int(user_id))
        return True

    # 添加用户下线方法
    def user_offline(self, user_id):
        """标记用户为离线状态"""
        self.online_users.discard(int(user_id))
        return True

    # 添加检查用户是否在线的方法
    def is_user_online(self, user_id):
        """检查用户是否在线"""
        return int(user_id) in self.online_users

    def get_user_contact_remarks(self, user_id):
        """获取用户对联系人的备注信息"""
        import os
        import json

        remarks = {}
        folder_path = "contacts"
        file_path = os.path.join(folder_path, f"contacts_{user_id}.json")

        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    remarks = json.load(f)
            except Exception as e:
                print(f"读取联系人备注失败: {e}")

        return remarks

    def update_contact_remark(self, user_id, contact_id, remark):
        """更新用户对联系人的备注"""
        import os
        import json

        folder_path = "contacts"
        os.makedirs(folder_path, exist_ok=True)
        file_path = os.path.join(folder_path, f"contacts_{user_id}.json")

        # 读取现有备注
        remarks = {}
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    remarks = json.load(f)
            except Exception:
                remarks = {}

        # 更新备注
        remarks[str(contact_id)] = {
            "remark": remark,
            "updated_at": _get_now()
        }

        # 保存备注
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(remarks, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            raise Exception(f"保存备注失败: {e}")

    def get_messages_between_users(self, user1_id, user2_id):
        """获取两个用户之间的所有消息"""
        query = """
        SELECT m.*, u1.Username as sender_name, u2.Username as receiver_name
        FROM messages m
        JOIN Users u1 ON m.sender_id = u1.UserID
        JOIN Users u2 ON m.receiver_id = u2.UserID
        WHERE ((m.sender_id = %s AND m.receiver_id = %s AND m.visible_to_sender = TRUE) 
           OR (m.sender_id = %s AND m.receiver_id = %s AND m.visible_to_receiver = TRUE))
        ORDER BY m.timestamp ASC
        """
        return self._fetchall(query, (user1_id, user2_id, user2_id, user1_id))

    def send_message(self, sender_id, receiver_id, content):
        """发送消息"""
        # 限制消息长度为250字
        if len(content) > 250:
            content = content[:250]

        query = """
        INSERT INTO messages (sender_id, receiver_id, content, timestamp, visible_to_sender, visible_to_receiver)
        VALUES (%s, %s, %s, %s, TRUE, TRUE)
        """
        timestamp = _get_now()
        self._execute(query, (sender_id, receiver_id, content, timestamp))

        return True

    def delete_contact(self, user_id, contact_id):
        """删除联系人（隐藏聊天记录）"""
        # 将用户与该联系人的聊天记录对自己设为不可见
        query = """
        UPDATE messages 
        SET visible_to_sender = FALSE 
        WHERE sender_id = %s AND receiver_id = %s
        """
        self._execute(query, (user_id, contact_id))

        query = """
        UPDATE messages 
        SET visible_to_receiver = FALSE 
        WHERE receiver_id = %s AND sender_id = %s
        """
        self._execute(query, (user_id, contact_id))

        # 删除备注信息
        import os
        import json

        folder_path = "contacts"
        file_path = os.path.join(folder_path, f"contacts_{user_id}.json")

        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    remarks = json.load(f)

                # 删除备注
                if str(contact_id) in remarks:
                    del remarks[str(contact_id)]

                    # 保存更新后的备注
                    with open(file_path, "w", encoding="utf-8") as f:
                        json.dump(remarks, f, ensure_ascii=False, indent=2)
            except Exception:
                pass  # 忽略备注删除错误

        return True

    def add_contact(self, user_id, contact_id):
        """添加联系人"""
        # 检查是否已经存在消息记录
        query = """
        SELECT COUNT(*) as count FROM messages 
        WHERE (sender_id = %s AND receiver_id = %s) OR (sender_id = %s AND receiver_id = %s)
        """
        result = self._fetchone(query, (user_id, contact_id, contact_id, user_id))

        # 如果没有消息记录，则添加一条系统消息
        if result['count'] == 0:
            system_message = "系统消息：你们已成为联系人，可以开始聊天了。"
            query = """
            INSERT INTO messages (sender_id, receiver_id, content, timestamp, visible_to_sender, visible_to_receiver)
            VALUES (%s, %s, %s, %s, TRUE, TRUE)
            """
            timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            self._execute(query, (contact_id, user_id, system_message, timestamp))

        return True

    def give_gift(self, sender_id, receiver_id, gift_type):
        """赠与礼物"""
        import datetime
        import os
        import json

        # 获取今天的日期
        today = datetime.date.today().strftime("%Y-%m-%d")

        # 检查今日赠与记录
        gift_info = self.get_user_gift_info(sender_id)

        # 检查是否达到上限
        if gift_type == "coin":
            if gift_info["coins_given_today"] >= 5:
                return {"success": False, "message": "今日金币赠与已达上限"}
        elif gift_type == "star":
            if gift_info["stars_given_today"] >= 1:
                return {"success": False, "message": "今日星星赠与已达上限"}

        # 获取发送者和接收者信息
        sender = self.get_user_by_id(sender_id)
        receiver = self.get_user_by_id(receiver_id)

        if not sender or not receiver:
            return {"success": False, "message": "用户不存在"}

        # 检查发送者是否有足够的礼物
        if gift_type == "coin" and sender["Coins"] < 1:
            return {"success": False, "message": "金币不足"}
        elif gift_type == "star" and sender["Stars"] < 1:
            return {"success": False, "message": "星星不足"}

        # 执行赠与
        amount = 1
        if gift_type == "coin":
            # 更新发送者和接收者的金币
            self._execute("UPDATE Users SET Coins = Coins - %s WHERE UserID = %s", (amount, sender_id))
            self._execute("UPDATE Users SET Coins = Coins + %s WHERE UserID = %s", (amount, receiver_id))
        elif gift_type == "star":
            # 更新发送者和接收者的星星
            self._execute("UPDATE Users SET Stars = Stars - %s WHERE UserID = %s", (amount, sender_id))
            self._execute("UPDATE Users SET Stars = Stars + %s WHERE UserID = %s", (amount, receiver_id))

        # 记录赠与到本地文件
        gift_record = {
            "sender_id": sender_id,
            "receiver_id": receiver_id,
            "gift_type": gift_type,
            "amount": amount,
            "gift_date": today,
            "timestamp": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }

        # 创建赠与记录目录
        os.makedirs("gift_records", exist_ok=True)
        file_name = f"gift_records/{today}.json"

        # 读取现有记录
        records = []
        if os.path.exists(file_name):
            try:
                with open(file_name, 'r', encoding='utf-8') as f:
                    records = json.load(f)
            except Exception:
                records = []

        # 添加新记录
        records.append(gift_record)

        # 保存记录
        try:
            with open(file_name, 'w', encoding='utf-8') as f:
                json.dump(records, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存赠与记录失败: {e}")

        return {
            "success": True,
            "amount": amount,
            "sender_name": sender["Nickname"],
            "receiver_name": receiver["Nickname"]
        }

    def get_user_gift_info(self, user_id):
        """获取用户今日赠与信息"""
        import datetime
        import os
        import json

        today = datetime.date.today().strftime("%Y-%m-%d")
        file_name = f"gift_records/{today}.json"

        coins_given = 0
        stars_given = 0

        # 读取今日赠与记录
        if os.path.exists(file_name):
            try:
                with open(file_name, 'r', encoding='utf-8') as f:
                    records = json.load(f)

                # 统计用户今日赠与
                for record in records:
                    if record["sender_id"] == user_id:
                        if record["gift_type"] == "coin":
                            coins_given += record["amount"]
                        elif record["gift_type"] == "star":
                            stars_given += record["amount"]
            except Exception as e:
                print(f"读取赠与记录失败: {e}")

        return {
            "coins_given_today": coins_given,
            "stars_given_today": stars_given,
            "coin_limit": 5,
            "star_limit": 1
        }

    def _save_message_to_file(self, sender_id, receiver_id, content, timestamp):
        """将消息保存到本地文件"""
        # 删除此方法，因为所有消息都存储在服务端数据库中
        pass

    def get_all_messages_for_user(self, user_id):
        """获取用户的所有消息（用于备份或恢复）"""
        query = """
        SELECT m.*, u1.Username as sender_name, u2.Username as receiver_name
        FROM messages m
        JOIN Users u1 ON m.sender_id = u1.UserID
        JOIN Users u2 ON m.receiver_id = u2.UserID
        WHERE m.sender_id = %s OR m.receiver_id = %s
        ORDER BY m.timestamp ASC
        """
        return self._fetchall(query, (user_id, user_id))

    # ---------- 角色 ----------
    def get_role_by_uid(self, uid):
        row = self._fetchone("SELECT RoleID FROM UserRoles_Con WHERE UserID=%s", (uid,))
        return row['RoleID'] if row else 3

    def update_user_role(self, uid, role_id):
        self._execute("UPDATE UserRoles_Con SET RoleID=%s WHERE UserID=%s", (role_id, uid))
        return True

    # ---------- 白名单 ----------
    def get_whitelist_state(self, uid):
        row = self._fetchone("SELECT WhiteState FROM PlayerData WHERE UserID=%s", (uid,))
        return row['WhiteState'] if row else 0  # 修复：处理None值，返回默认值0

    def add_to_whitelist(self, uid):
        name_row = self._fetchone("SELECT PlayerName FROM PlayerData WHERE UserID=%s", (uid,))
        if not name_row:
            return "未找到玩家名"
        name = name_row['PlayerName']
        _rcon(f"whitelist add {name}")
        self._execute("UPDATE PlayerData SET WhiteState=1, PassDate=%s WHERE UserID=%s", (_get_now()[:10], uid))
        return True

    def remove_whitelist(self, uid):
        name_row = self._fetchone("SELECT PlayerName FROM PlayerData WHERE UserID=%s", (uid,))
        if name_row:
            _rcon(f"whitelist remove {name_row['PlayerName']}")
        self._execute("UPDATE PlayerData SET WhiteState=0 WHERE UserID=%s", (uid,))
        return True

    def get_whitelist_applications(self, uid):
        """
        获取用户的白名单申请记录
        """
        import os
        import json
        from datetime import datetime

        applications = []

        # 检查申请记录目录是否存在
        if os.path.exists("whitelist_applications"):
            # 遍历所有申请记录文件
            for filename in os.listdir("whitelist_applications"):
                if filename.startswith(f"{uid}_") and filename.endswith(".json"):
                    filepath = os.path.join("whitelist_applications", filename)
                    try:
                        with open(filepath, "r", encoding="utf-8") as f:
                            app_data = json.load(f)
                            applications.append(app_data)
                    except Exception:
                        continue

        # 按申请时间排序（最新的在前）
        applications.sort(key=lambda x: x["date"], reverse=True)

        return applications

    # ---------- 签到 ----------
    def has_sign_today(self, uid):
        # 修改为与app.py一致的逻辑，使用文件检查而非数据库表
        import os
        from datetime import date
        today_date = date.today().strftime('%Y-%m-%d')
        file_name = f"签到日志/{today_date}.txt"
        try:
            with open(file_name, 'r') as file:
                lines_in_file = file.readlines()
            lines_in_file = [line.strip() for line in lines_in_file]
            return str(uid) in lines_in_file
        except FileNotFoundError:
            return False

    def do_sign(self, uid):
        # 修改为与app.py一致的逻辑
        import os
        import random
        from datetime import date

        # 检查今日是否已签到
        if self.has_sign_today(uid):
            return None

        # 写入签到日志文件
        today_date = date.today().strftime('%Y-%m-%d')
        file_name = f"签到日志/{today_date}.txt"
        os.makedirs(os.path.dirname(file_name), exist_ok=True)
        with open(file_name, 'a') as file:
            file.write(f"{uid}\n")

        # 获取用户角色
        role = self.get_role_by_uid(uid)

        # 计算签到奖励
        coin = random.randint(1, 10)
        star = 0

        # VIP额外奖励
        if role <= 2:
            coin += random.randint(1, 5)

        # 星星奖励
        stars_random = random.randint(0, 100)
        if stars_random < 5:  # 5%获得1颗星星
            star = 1
        elif stars_random == 99:  # 1%获得5颗星星
            star = 5

        # 更新用户数据
        self._execute("UPDATE Users SET Coins=Coins+%s, Stars=Stars+%s WHERE UserID=%s", (coin, star, uid))

        return {"coin": coin, "star": star}

    # ---------- 排行榜 ----------
    def coin_leaderboard(self):
        return self._fetchall("SELECT UserID, Nickname, Coins FROM Users ORDER BY Coins DESC LIMIT 100")

    def star_leaderboard(self):
        return self._fetchall("SELECT UserID, Nickname, Stars FROM Users ORDER BY Stars DESC LIMIT 100")

    # ---------- 登录日志 ----------
    def log_login(self, uid, ip, address):
        self._execute("INSERT INTO UserLoginRecords (UserID, IPAddress, Address) VALUES (%s,%s,%s)",
                      (uid, ip, address))

    # ---------- QQ 绑定 ----------
    def get_qq_by_uid(self, uid):
        r = self._fetchone("SELECT QQID FROM UserQQ_Con WHERE UserID=%s", (uid,))
        return r['QQID'] if r else None

    def bind_qq(self, uid, qq):
        self._execute("INSERT INTO UserQQ_Con (UserID, QQID) VALUES (%s,%s) ON DUPLICATE KEY UPDATE QQID=%s",
                      (uid, qq, qq))
        return True

    # ---------- 通用 ----------
    def get_playername(self, uid):
        r = self._fetchone("SELECT PlayerName FROM PlayerData WHERE UserID=%s", (uid,))
        return r['PlayerName'] if r else ""

    def get_uuid(self, uid):
        r = self._fetchone("SELECT uuid FROM PlayerData WHERE UserID=%s", (uid,))
        return r['uuid'] if r else ""

    def raw_query(self, sql, params=None):
        """仅供后台查询/更新使用（Owner 权限）"""
        if sql.strip().lower().startswith("select"):
            return self._fetchall(sql, params)
        else:
            self._execute(sql, params)
            return True