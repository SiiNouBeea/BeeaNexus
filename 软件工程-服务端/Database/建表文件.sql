-- Users 表
CREATE TABLE Users (
    UserID INT AUTO_INCREMENT PRIMARY KEY,
    Username VARCHAR(50) NOT NULL,
    Password VARCHAR(128) NOT NULL,
    Nickname VARCHAR(50),
    Email VARCHAR(100),
    Phone VARCHAR(20),
    CreatedAt DATETIME,
    UpdatedAt DATETIME,
    last_online DATETIME,
    Coins INT DEFAULT 0,
    Stars INT DEFAULT 0
);

-- UserRoles 表
CREATE TABLE UserRoles (
    RoleID INT AUTO_INCREMENT PRIMARY KEY,
    RoleName VARCHAR(50) NOT NULL
);

-- UserRoles_Con 表
CREATE TABLE UserRoles_Con (
    UserID INT NOT NULL,
    RoleID INT NOT NULL,
    PRIMARY KEY (UserID, RoleID),
    FOREIGN KEY (UserID) REFERENCES Users(UserID),
    FOREIGN KEY (RoleID) REFERENCES UserRoles(RoleID)
);

-- PlayerData 表
CREATE TABLE PlayerData (
    PlayerID INT AUTO_INCREMENT PRIMARY KEY,
    UserID INT NOT NULL,
    PlayerName CHAR(32),
    uuid CHAR(32),
    WhiteState BIT(1) DEFAULT 0,
    Genuine BIT(1) DEFAULT 0,
    PassDate DATE,
    FOREIGN KEY (UserID) REFERENCES Users(UserID)
);

-- messages 表
CREATE TABLE messages (
    MessageID INT AUTO_INCREMENT PRIMARY KEY,
    sender_id INT NOT NULL,
    receiver_id INT NOT NULL,
    content TEXT,
    timestamp DATETIME,
    visible_to_sender TINYINT(1) DEFAULT 1,
    visible_to_receiver TINYINT(1) DEFAULT 1,
    is_read TINYINT(1) DEFAULT 0,
    FOREIGN KEY (sender_id) REFERENCES Users(UserID),
    FOREIGN KEY (receiver_id) REFERENCES Users(UserID)
);

-- UserProfiles 表
CREATE TABLE UserProfiles (
    UserID INT PRIMARY KEY,
    FirstName VARCHAR(50),
    LastName VARCHAR(50),
    Birthday DATE,
    Gender VARCHAR(10),
    Bio VARCHAR(255),
    FOREIGN KEY (UserID) REFERENCES Users(UserID)
);

-- UserQQ_Con 表
CREATE TABLE UserQQ_Con (
    UserID INT NOT NULL,
    QQID CHAR(16) NOT NULL,
    PRIMARY KEY (UserID, QQID),
    FOREIGN KEY (UserID) REFERENCES Users(UserID)
);

-- UserLoginRecords 表
CREATE TABLE UserLoginRecords (
    RecordID INT AUTO_INCREMENT PRIMARY KEY,
    UserID INT NOT NULL,
    LoginTime DATETIME,
    IPAddress VARCHAR(15),
    Address VARCHAR(100),
    FOREIGN KEY (UserID) REFERENCES Users(UserID)
);
