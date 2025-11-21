# Epilepsy-Dataset

基于 **Django** 的癫痫数据集管理门户网站。

本仓库包含两个子目录：

- `epilepsy_portal/` – 实际的 Django 项目（我们开发的癫痫数据集网站）
- `argon-dashboard-django/` – 原始的 **Argon Dashboard Django** 仓库，用作前端界面与静态资源的参考与来源（不会直接运行）

---

## 功能概览

### 1. 全局总览（Dashboard｜全局总览）

- 展示服务器运行状态：
  - CPU 使用率（百分比）
  - 内存使用率（已用 / 总量）
  - 磁盘使用率（已用 / 总量）
- 使用卡片式（Card）布局，基于 Argon Dashboard 的样式。

### 2. 患者管理

**新建患者（新建患者）**

- 表单主要字段：
  - 患者姓名
  - 性别
  - 生日（日期选择控件）
  - 左右利手
  - 科室
  - 床号
  - 入院时间（日期选择控件）

**浏览患者（浏览患者）**

- 功能：
  - 列出所有患者基本信息（姓名 / 性别 / 生日 / 床号 / 入院时间）
  - 搜索（根据姓名 / 科室 / 床号模糊搜索）
  - 分页显示
  - 在列表中对单个患者进行：
    - 修改（侧滑面板 Offcanvas 编辑）
    - 删除（POST 请求）

- 对不同角色展示不同操作按钮：
  - 管理员、工作人员：可以“修改 / 删除”
  - 访客：只读，显示“只读”，不显示修改 / 删除按钮

### 3. 用户管理（管理设置）

- 管理员专用页面：
  - 查看所有用户列表
  - 查看每个用户的角色（管理员 / 工作人员 / 访客）与启用状态
- 后续可扩展：在前端直接新建 / 编辑 / 禁用用户。

### 4. 角色与权限

通过自定义 `UserProfile` 模型实现三种角色：

- **管理员（Admin / 管理员）**
  - 访问所有功能：
    - 全局总览
    - 浏览患者
    - 新建 / 修改 / 删除患者
    - 管理设置（用户管理）

- **工作人员（Staff / 工作人员）**
  - 可以：
    - 全局总览
    - 浏览患者
    - 新建 / 修改 / 删除患者
  - 不可以：
    - 管理其他用户（不可访问“管理设置”页面）

- **访客（Guest / 访客）**
  - 可以：
    - 全局总览
    - 浏览患者（只读）
  - 不可以：
    - 新建 / 修改 / 删除患者
    - 管理用户

权限控制方式：

- 视图层：通过自定义 `RoleRequiredMixin` + Django 的 `LoginRequiredMixin`
- 模板层：通过 `user.profile.role` 或 `user.profile.is_admin / is_staff_or_admin` 控制菜单和按钮显示

---

## 项目结构

仓库根目录：

```text
Epilepsy-Dataset/
├─ epilepsy_portal/          # 主 Django 项目
│  ├─ manage.py
│  ├─ epilepsy_portal/       # settings.py, urls.py, wsgi.py 等
│  ├─ epilepsy/              # 业务 App：模型、视图、表单、模板等
│  ├─ templates/             # 根模板目录：layouts、includes、registration、epilepsy 等
│  ├─ static/ 或 staticfiles/  # 静态资源目录（包含 Argon 的 assets/）
│  └─ ...
└─ argon-dashboard-django/   # Argon Dashboard Django 原仓库（参考用）
