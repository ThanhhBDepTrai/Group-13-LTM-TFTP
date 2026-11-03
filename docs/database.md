# Database (`database/database.py`)
Bảng: servers, workspaces, roles, permissions, role_permissions, departments, positions,
ent_users, employees, folders, files, tags, file_tags, file_permissions, tasks,
task_assignments, join_codes, sessions, audit_logs. Bảng người dùng enterprise là
`ent_users` để không đụng `users` của `auth.py` cũ (tương thích ngược).
Quyền thực tế = `role_permissions`; không kế thừa theo cấp bậc. ACL file: deny mặc định,
chủ file và SUPER_ADMIN luôn có quyền, ACL ở đường dẫn sâu nhất thắng.
