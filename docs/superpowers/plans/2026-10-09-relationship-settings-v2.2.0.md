# 关系设定 v2.2.0 Implementation Plan

> **For agentic workers:** Execute the tasks in order, keep each change test-driven, and commit after every independently verified slice.

**Goal:** 为本地伴侣加入完整、可持久化的关系设定，让用户配置“露娜”的名字、称呼方式和回复行为，并在对话提示词、字幕、历史记录、导出与清理流程中一致生效。

**Architecture:** 后端以 SQLite 单例配置作为唯一事实来源，通过受限 PATCH/重置 API 更新；独立 `RelationshipPromptBuilder` 把结构化选项编译成有长度上限的系统提示词。前端由 App 持有当前配置，关系设定页负责编辑和预览，沉浸式对话组件只消费已保存的伴侣名字。所有设置只影响后续回复，不修改历史消息。

**Tech Stack:** FastAPI、Pydantic、SQLite、pytest；React、TypeScript、Vite、Vitest/Testing Library。

**Spec:** `docs/superpowers/specs/2026-10-09-relationship-settings-design-v2.2.0.md`

## Global Constraints

- 不新增模型、常驻进程或 GPU 负载。
- 不允许用户注入任意系统提示词；只接受枚举、限长文本和最多 8 个屏蔽词。
- API 写操作继续使用会话令牌、同源校验、幂等键和乐观版本号。
- 关系提示词不超过 600 个 Unicode 字符。
- 所有修改使用小步测试；每个任务通过后提交 Git。

## Task 1: 数据库存储与 Repository 接口

**Files:**

- Modify: `backend/app/db.py`
- Modify: `backend/app/services.py`
- Create: `backend/tests/test_relationship_repository.py`

**Steps:**

1. 先写失败测试，覆盖默认配置、局部更新、版本递增、版本冲突、重置、幂等重放、导出和清理恢复默认值。
2. 在 SQLite schema 中新增 `relationship_settings` 单例表和 schema migration v2，启动时插入默认行。
3. 在 Repository 中加入读取、局部更新和重置方法；JSON 编解码屏蔽词，事务内检查 `expected_version`。
4. 将关系设定加入本地数据导出；清理数据时恢复默认配置。
5. 运行：`backend/.venv/Scripts/python.exe -m pytest backend/tests/test_relationship_repository.py -q`
6. 提交：`git commit -am "feat: persist relationship settings"`，并显式加入新测试文件。

## Task 2: 关系设定 API 与输入边界

**Files:**

- Modify: `backend/app/schemas.py`
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_relationship_api.py`

**Steps:**

1. 先写失败测试，覆盖 GET、PATCH、reset、非法枚举、超长文本、过多屏蔽词、缺少/错误会话令牌、跨源请求、版本冲突和幂等重试。
2. 添加严格 Pydantic 模型：禁止额外字段，文本去空白，限制长度，屏蔽词去重并最多 8 项。
3. 添加 `/api/relationship-settings` GET/PATCH 和 `/api/relationship-settings/reset` POST。
4. 将仓储冲突映射为稳定的 409 错误码，将校验失败保持为 422。
5. 运行：`backend/.venv/Scripts/python.exe -m pytest backend/tests/test_relationship_api.py -q`
6. 提交：`git commit -am "feat: expose relationship settings api"`。

## Task 3: 结构化提示词与对话接入

**Files:**

- Create: `backend/app/relationship.py`
- Modify: `backend/app/services.py`
- Create: `backend/tests/test_relationship_prompt.py`
- Modify: `backend/tests/test_api.py`

**Steps:**

1. 先写失败测试，覆盖每种行为选项的提示词映射、昵称频率、空昵称、屏蔽词约束、确定性输出和 600 字符上限。
2. 实现纯函数式 `RelationshipPromptBuilder`，只从经过校验的结构化字段生成提示词。
3. 在每次提交消息时读取一次当前关系设定，并传给本地兜底回复与 Ollama 适配器。
4. 保留既有记忆提示和安全边界；模型不可用时，兜底回复也使用配置的自称和用户昵称。
5. 运行：`backend/.venv/Scripts/python.exe -m pytest backend/tests/test_relationship_prompt.py backend/tests/test_api.py -q`
6. 提交：`git commit -am "feat: apply relationship settings to replies"`。

## Task 4: 前端类型、客户端与状态管理

**Files:**

- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/api/client.test.ts`

**Steps:**

1. 先写失败测试，验证关系设定 GET/PATCH/reset 的请求路径、方法、令牌、Origin、幂等键和请求体。
2. 添加 `RelationshipSettings` 及更新输入类型和默认值。
3. App 启动时加载设置；保存或重置后更新单一状态源。
4. 网络失败时保留安全默认值，不阻塞文字聊天。
5. 运行：`npm test -- --run src/api/client.test.ts`（工作目录 `frontend`）。
6. 提交：`git commit -am "feat: add relationship settings client state"`。

## Task 5: 完整关系设定页面

**Files:**

- Create: `frontend/src/components/RelationshipSettings.tsx`
- Create: `frontend/src/components/RelationshipSettings.test.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`

**Steps:**

1. 先写失败的交互测试：载入值、编辑、静态预览、保存、未保存状态、重置确认、服务端冲突提示和输入限制。
2. 实现分组表单：身份称呼、关系氛围、回复行为、开场白和屏蔽词。
3. 保存按钮只提交已变更字段；重置使用当前版本号；保存期间防止重复请求。
4. 页面使用半透明面板并维持现有背景视觉；小屏布局可滚动且控件可触达。
5. 运行：`npm test -- --run src/components/RelationshipSettings.test.tsx`（工作目录 `frontend`）。
6. 提交：`git commit -am "feat: build relationship settings page"`。

## Task 6: 动态身份展示与全量验证

**Files:**

- Modify: `frontend/src/components/ImmersiveChat.tsx`
- Modify: `frontend/src/components/ImmersiveChat.test.tsx`
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-10-09-relationship-settings-design-v2.2.0.md`（仅记录最终实现偏差，如有）

**Steps:**

1. 先写失败测试，确认字幕、历史消息标签和无障碍名称使用当前伴侣名，且旧消息正文不被改写。
2. 为 `ImmersiveChat` 增加 `companionName` 属性并替换硬编码“露娜”；保留缺省回退。
3. 更新 README：配置入口、字段范围、数据位置、导出/清理行为和部署影响。
4. 运行后端全量测试：`backend/.venv/Scripts/python.exe -m pytest backend/tests -q`。
5. 运行前端全量测试与构建：`npm test -- --run`、`npm run build`（工作目录 `frontend`）。
6. 启动本地服务，完成一次 GET → PATCH → 文字回复 → reset 的真实冒烟测试，并确认设置不会增加常驻进程或模型占用。
7. 提交：`git commit -am "docs: complete relationship settings v2.2.0"`。

## Review Focus

- 数据库升级在已有用户数据上是否幂等。
- 幂等重试是否先于版本冲突返回原结果。
- Prompt Builder 是否能被自由文本突破系统边界，或超过 600 字符。
- 前端发生 409 时是否保留用户未保存的编辑内容。
- 动态名称是否覆盖字幕、历史记录和无障碍标签，但不改写历史正文。
- 导出和清理是否包含关系设定，且不泄露会话令牌。
