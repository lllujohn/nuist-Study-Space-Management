import re

with open("index.html", "r") as f:
    content = f.read()

# 1. Remove profile input
content = re.sub(
    r'<el-input v-model="queryStudentId".*?</el-input>',
    '',
    content,
    flags=re.DOTALL
)

# 2. Remove my_reservations input
content = re.sub(
    r'<el-input v-model="resStudentId".*?</el-input>',
    '',
    content,
    flags=re.DOTALL
)
content = content.replace('v-if="myReservations.length > 0"', 'v-if="myReservations.length > 0 || hasSearchedRes"')

# 3. Remove student_id input from reserveDialog
content = re.sub(
    r'<el-form-item label="你的学号">\s*<el-input v-model="reserveForm.student_id".*?</el-form-item>',
    '',
    content,
    flags=re.DOTALL
)

# 4. Remove student_id input from exchangeDialog
content = re.sub(
    r'<el-form-item label="你的学号">\s*<el-input v-model="exchangeForm.student_id".*?</el-form-item>',
    '',
    content,
    flags=re.DOTALL
)

# 5. Remove student_id input from repair section
content = re.sub(
    r'<el-form-item label="学号">\s*<el-input v-model="repairForm.student_id".*?</el-form-item>',
    '',
    content,
    flags=re.DOTALL
)

# 6. Update Vue script variables and methods
content = content.replace("const queryStudentId = ref('');", "")
content = content.replace("const resStudentId = ref('');", "")

content = content.replace(
    "if (!queryStudentId.value) return ElMessage.warning('请输入学号');",
    "if (!currentUser.value || !currentUser.value.student_id) return ElMessage.warning('未登录学号');\n          const sid = currentUser.value.student_id;"
)
content = content.replace("apiFetch(`${API_BASE}/api/students/${queryStudentId.value}`)", "apiFetch(`${API_BASE}/api/students/${sid}`)")


content = content.replace(
    "if (!resStudentId.value) return ElMessage.warning('请输入学号');",
    "if (!currentUser.value || !currentUser.value.student_id) return;\n          const sid = currentUser.value.student_id;"
)
content = content.replace("apiFetch(`${API_BASE}/api/reservations/${resStudentId.value}`)", "apiFetch(`${API_BASE}/api/reservations/${sid}`)")
content = content.replace("student_id: resStudentId.value", "student_id: sid")


content = content.replace(
    "if (!STUDENT_ID_RE.test(reserveForm.value.student_id)) return ElMessage.error(\"学号格式不正确\");",
    "reserveForm.value.student_id = currentUser.value.student_id;"
)
content = content.replace(
    "if (!STUDENT_ID_RE.test(exchangeForm.value.student_id)) return ElMessage.error(\"学号格式不正确\");",
    "exchangeForm.value.student_id = currentUser.value.student_id;"
)
content = content.replace(
    "if (!STUDENT_ID_RE.test(repairForm.value.student_id)) return ElMessage.error(\"学号格式不正确\");",
    "repairForm.value.student_id = currentUser.value.student_id;"
)

# 7. Update watch activeMenu to fetch automatically
watch_old = """        watch(activeMenu, (val) => {
          if (val === 'dashboard') {
            nextTick(() => { if(!chartOcc) initCharts(); else { chartOcc.resize(); chartTrd.resize(); }});
          }
        });"""
watch_new = """        watch(activeMenu, (val) => {
          if (val === 'dashboard') {
            nextTick(() => { if(!chartOcc) initCharts(); else { chartOcc.resize(); chartTrd.resize(); }});
          }
          if (val === 'profile' && currentUser.value && currentUser.value.student_id) {
            fetchProfile();
          }
          if (val === 'my_reservations' && currentUser.value && currentUser.value.student_id) {
            fetchMyReservations();
          }
        });"""
content = content.replace(watch_old, watch_new)

# Remove unused exports in return
content = content.replace("queryStudentId, ", "")
content = content.replace("resStudentId, ", "")

with open("index.html", "w") as f:
    f.write(content)

print("Patch applied.")
