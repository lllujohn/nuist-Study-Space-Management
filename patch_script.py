import re

with open("index.html", "r") as f:
    content = f.read()

# Replace the beginning of setup()
setup_start_marker = "      setup() {"
setup_new_start = """      setup() {
        // ================== Auth State ==================
        const token = ref(localStorage.getItem('token') || '');
        const currentUser = ref(JSON.parse(localStorage.getItem('user') || 'null'));
        const isLoggedIn = computed(() => !!token.value);
        const isAdmin = computed(() => currentUser.value && currentUser.value.role && currentUser.value.role.startsWith('admin'));
        
        const loginTab = ref('student');
        const loginLoading = ref(false);
        const loginForm = ref({ student_id: '', password: '' });
        const regForm = ref({ student_id: '', name: '', phone: '', password: '' });
        const adminForm = ref({ username: '', password: '' });
        const resetForm = ref({ student_id: '', phone: '', new_password: '' });

        const setLoginSession = (data) => {
          token.value = data.token;
          currentUser.value = data;
          localStorage.setItem('token', data.token);
          localStorage.setItem('user', JSON.stringify(data));
          activeMenu.value = isAdmin.value ? 'dashboard' : 'spaces';
          if (isAdmin.value) { loadAnnouncements(); } else { fetchAnnouncements(); }
        };

        const doStudentLogin = async () => {
          if (!loginForm.value.student_id || !loginForm.value.password) return ElMessage.warning('请填写完整');
          loginLoading.value = true;
          try {
            const r = await fetch(API_BASE + '/api/auth/login', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(loginForm.value)
            });
            const res = await r.json();
            if (res.code === 200) { ElMessage.success('登录成功'); setLoginSession(res.data); }
            else { ElMessage.error(res.detail || res.msg || '登录失败'); }
          } finally { loginLoading.value = false; }
        };

        const doAdminLogin = async () => {
          if (!adminForm.value.username || !adminForm.value.password) return ElMessage.warning('请填写完整');
          loginLoading.value = true;
          try {
            const r = await fetch(API_BASE + '/api/auth/admin/login', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(adminForm.value)
            });
            const res = await r.json();
            if (res.code === 200) { ElMessage.success('管理员登录成功'); setLoginSession(res.data); }
            else { ElMessage.error(res.detail || res.msg || '登录失败'); }
          } finally { loginLoading.value = false; }
        };

        const doRegister = async () => {
          if (!regForm.value.student_id || !regForm.value.name || !regForm.value.phone || !regForm.value.password) return ElMessage.warning('请填写完整');
          loginLoading.value = true;
          try {
            const r = await fetch(API_BASE + '/api/auth/register', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(regForm.value)
            });
            const res = await r.json();
            if (res.code === 200) { ElMessage.success('注册成功，请登录'); loginTab.value = 'student'; loginForm.value.student_id = regForm.value.student_id; }
            else { ElMessage.error(res.detail || res.msg || '注册失败'); }
          } finally { loginLoading.value = false; }
        };

        const doResetPwd = async () => {
          if (!resetForm.value.student_id || !resetForm.value.phone || !resetForm.value.new_password) return ElMessage.warning('请填写完整');
          loginLoading.value = true;
          try {
            const r = await fetch(API_BASE + '/api/auth/reset-password', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(resetForm.value)
            });
            const res = await r.json();
            if (res.code === 200) { ElMessage.success('密码已重置，请登录'); loginTab.value = 'student'; }
            else { ElMessage.error(res.detail || res.msg || '验证失败'); }
          } finally { loginLoading.value = false; }
        };

        const doLogout = () => {
          token.value = '';
          currentUser.value = null;
          localStorage.removeItem('token');
          localStorage.removeItem('user');
          activeMenu.value = 'spaces';
        };

        // ================== Admin State & Methods ==================
        const adminLoading = ref(false);
        const adminRooms = ref([]);
        const adminTickets = ref([]);
        const adminUsers = ref([]);
        const adminLogs = ref([]);
        const announcements = ref([]);

        const authHeaders = () => ({ 'Authorization': 'Bearer ' + token.value, 'Content-Type': 'application/json' });
        const apiFetch = async (url, options = {}) => {
          if (!options.headers) options.headers = {};
          options.headers['Authorization'] = 'Bearer ' + token.value;
          const r = await fetch(url, options);
          if (r.status === 401 || r.status === 403) { doLogout(); ElMessage.error('会话已过期，请重新登录'); throw new Error('Unauthorized'); }
          return r;
        };

        const loadAnnouncements = async () => {
          const r = await apiFetch(API_BASE + '/api/announcements');
          const res = await r.json();
          if (res.code === 200) announcements.value = res.data;
        };
        const fetchAnnouncements = loadAnnouncements;

        const loadAdminRooms = async () => {
          adminLoading.value = true;
          const r = await apiFetch(API_BASE + '/api/admin/rooms');
          const res = await r.json();
          adminLoading.value = false;
          if (res.code === 200) adminRooms.value = res.data;
        };

        const loadAdminTickets = async () => {
          adminLoading.value = true;
          const r = await apiFetch(API_BASE + '/api/admin/tickets');
          const res = await r.json();
          adminLoading.value = false;
          if (res.code === 200) adminTickets.value = res.data;
        };

        const loadAdminUsers = async () => {
          adminLoading.value = true;
          const r = await apiFetch(API_BASE + '/api/admin/students');
          const res = await r.json();
          adminLoading.value = false;
          if (res.code === 200) adminUsers.value = res.data;
        };

        const loadAdminLogs = async () => {
          adminLoading.value = true;
          const r = await apiFetch(API_BASE + '/api/admin/logs');
          const res = await r.json();
          adminLoading.value = false;
          if (res.code === 200) adminLogs.value = res.data;
        };

        const openAddRoomDialog = () => {
          const name = prompt('请输入新阅览室名称：', '新阅览室');
          if (!name) return;
          const loc = prompt('请输入位置：', '某楼某层');
          if (!loc) return;
          apiFetch(API_BASE + '/api/admin/rooms', {
            method: 'POST', headers: authHeaders(),
            body: JSON.stringify({ room_name: name, location: loc, open_time: '08:00:00', close_time: '22:00:00' })
          }).then(r => r.json()).then(res => {
            if (res.code === 200) { ElMessage.success('添加成功'); loadAdminRooms(); }
            else ElMessage.error(res.detail || '失败');
          });
        };

        const openAdminSeatDrawer = (room) => {
          // Simplification: we just fetch seats and allow toggling maintenance
          // For now, let's just show standard UI
          drawerTitle.value = `管理 ${room.room_name} 的座位`;
          drawerVisible.value = true;
          seatsLoading.value = true;
          apiFetch(API_BASE + `/api/seats/${room.room_id}`).then(r => r.json()).then(res => {
            seatsLoading.value = false;
            if(res.code===200) {
                // Attach room for reference in template if needed
                seats.value = res.data;
            }
          });
        };

        const updateTicketStatus = async (ticket_id, action) => {
          const r = await apiFetch(API_BASE + `/api/admin/tickets/${ticket_id}`, {
            method: 'PATCH', headers: authHeaders(), body: JSON.stringify({ action })
          });
          const res = await r.json();
          if(res.code===200) { ElMessage.success('更新成功'); loadAdminTickets(); }
        };

        const adminBanUser = async (user) => {
          const reason = prompt('请输入拉黑原因：', '违反自习室规定');
          if (reason === null) return;
          const r = await apiFetch(API_BASE + `/api/admin/students/${user.student_id}/blacklist`, {
            method: 'PATCH', headers: authHeaders(), body: JSON.stringify({ action: 'ban', reason })
          });
          const res = await r.json();
          if(res.code===200) { ElMessage.success('已拉黑'); loadAdminUsers(); }
        };

        const adminUnbanUser = async (student_id) => {
          const r = await apiFetch(API_BASE + `/api/admin/students/${student_id}/blacklist`, {
            method: 'PATCH', headers: authHeaders(), body: JSON.stringify({ action: 'unban' })
          });
          const res = await r.json();
          if(res.code===200) { ElMessage.success('已解封'); loadAdminUsers(); }
        };

        const openCreditDialog = (user) => {
          const deltaStr = prompt(`为学生 ${user.name} 调整信用分 (输入正负数字)：`, '-5');
          if(!deltaStr) return;
          const delta = parseInt(deltaStr);
          if(isNaN(delta)) return ElMessage.error('输入无效');
          const reason = prompt('调整原因：', '管理员手动调整');
          apiFetch(API_BASE + `/api/admin/students/${user.student_id}/credit`, {
            method: 'PATCH', headers: authHeaders(), body: JSON.stringify({ delta, reason })
          }).then(r=>r.json()).then(res=>{
             if(res.code===200) { ElMessage.success('调整成功'); loadAdminUsers(); }
          });
        };

        const openAnnDialog = () => {
          const title = prompt('公告标题：');
          if(!title) return;
          const content = prompt('公告内容：');
          if(!content) return;
          apiFetch(API_BASE + '/api/admin/announcements', {
            method: 'POST', headers: authHeaders(), body: JSON.stringify({ title, content })
          }).then(r=>r.json()).then(res=>{
             if(res.code===200) { ElMessage.success('发布成功'); loadAnnouncements(); }
          });
        };

        const deleteAnnouncement = async (id) => {
          if(!confirm('确认删除公告？')) return;
          const r = await apiFetch(API_BASE + `/api/admin/announcements/${id}`, { method: 'DELETE', headers: authHeaders() });
          const res = await r.json();
          if(res.code===200) { ElMessage.success('已删除'); loadAnnouncements(); }
        };

"""
content = content.replace(setup_start_marker, setup_new_start)

# Replace fetch calls with apiFetch where applicable
# Be careful to not replace the auth fetches
content = content.replace("fetch(`${API_BASE}/api/dashboard", "apiFetch(`${API_BASE}/api/dashboard")
content = content.replace("fetch(`${API_BASE}/api/rooms", "apiFetch(`${API_BASE}/api/rooms")
content = content.replace("fetch(`${API_BASE}/api/seats", "apiFetch(`${API_BASE}/api/seats")
content = content.replace("fetch(`${API_BASE}/api/reserve", "apiFetch(`${API_BASE}/api/reserve")
content = content.replace("fetch(`${API_BASE}/api/products", "apiFetch(`${API_BASE}/api/products")
content = content.replace("fetch(`${API_BASE}/api/exchange", "apiFetch(`${API_BASE}/api/exchange")
content = content.replace("fetch(`${API_BASE}/api/repairs", "apiFetch(`${API_BASE}/api/repairs")
content = content.replace("fetch(`${API_BASE}/api/students", "apiFetch(`${API_BASE}/api/students")
content = content.replace("fetch(`${API_BASE}/api/reservations", "apiFetch(`${API_BASE}/api/reservations")

# Add the new variables to the return block
return_block_start = "        return {"
return_new_block = """        return {
          isLoggedIn, isAdmin, currentUser, loginTab, loginLoading, loginForm, regForm, adminForm, resetForm,
          doStudentLogin, doAdminLogin, doRegister, doResetPwd, doLogout,
          adminLoading, adminRooms, adminTickets, adminUsers, adminLogs, announcements,
          loadAdminRooms, loadAdminTickets, loadAdminUsers, loadAdminLogs, loadAnnouncements,
          openAddRoomDialog, openAdminSeatDrawer, updateTicketStatus, adminBanUser, adminUnbanUser, openCreditDialog, openAnnDialog, deleteAnnouncement,"""
content = content.replace(return_block_start, return_new_block)

# Add onMounted fetch Announcements
mounted_start = "        onMounted(() => {"
mounted_new = """        onMounted(() => {
          if (isLoggedIn.value) {
            if (isAdmin.value) loadAnnouncements();
            else fetchAnnouncements();
          }"""
content = content.replace(mounted_start, mounted_new)

with open("index.html", "w") as f:
    f.write(content)

print("Patch applied.")
