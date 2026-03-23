# 安蒂克服务管理系统

基于 Flet + SQLite 的本地单机预约管理系统。

## 功能

- 今日排班（技师 × 时段网格）
- 新增预约（支持会员 / 散客）
- 查询预约（按姓名、手机号、会员号搜索）
- 会员管理（添加会员、充值套餐、消费扣次）
- 技师管理（在职/停用、排休、删除）
- 服务项目管理

## 运行环境

- Python 3.10+
- Windows 10/11

## 快速开始（开发模式）

```bash
pip install -r requirements.txt
python app.py          # 桌面窗口模式
python app.py --web    # 浏览器模式（http://localhost:8550）
```

## 打包为 Windows 可执行文件

### 方法一：flet pack（推荐，最简单）

```bash
pip install flet
flet pack app.py --name "安蒂克服务管理系统" --add-data "assets;assets"
```

打包完成后，`dist/` 目录下会生成 `安蒂克服务管理系统.exe`。

### 方法二：PyInstaller（更灵活）

```bash
pip install pyinstaller
pyinstaller --onefile --windowed --name "安蒂克服务管理系统" --add-data "assets;assets" app.py
```

### 发布

将以下文件一起打包发给用户即可：

```
安蒂克服务管理系统.exe
assets/
  fonts/
    NotoSansSC.ttf
```

用户双击 exe 即可运行，数据库文件 `appointments.db` 会自动创建在 exe 同级目录下。

## 注意事项

- `appointments.db` 是数据文件，请勿删除，否则所有数据丢失
- 首次运行会自动创建数据库并预置 4 名技师和 4 个服务项目
