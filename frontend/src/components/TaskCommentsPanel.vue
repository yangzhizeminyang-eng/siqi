<script setup lang="ts">
import { Bell, BellRing, MessageSquareText, Send, Trash2 } from '@lucide/vue'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { ApiError, apiRequest } from '../services/api'
import { getDataSourceMode } from '../services/dataSourceMode'
import { useAuthStore } from '../stores/auth'
import type { TaskComment, TaskNotification } from '../types/domain'
import { notificationTypeText } from '../types/domain'
import { connectRealtime, subscribeRealtime } from '../services/realtime'

const props = defineProps<{
  taskId: string
  taskStatus: string
}>()

const auth = useAuthStore()
const currentUserId = computed(() => auth.displayUser?.id ?? '')
const comments = ref<TaskComment[]>([])
const notifications = ref<TaskNotification[]>([])
const body = ref('')
const sending = ref(false)
const nudging = ref(false)
const loading = ref(false)

const nudgeEligible = computed(() => props.taskStatus === 'WAITING_APPROVAL')
let unsubscribeRealtime: (() => void) | undefined

async function load() {
  if (getDataSourceMode() !== 'real') {
    return
  }
  loading.value = true
  try {
    const [commentList, notifList] = await Promise.all([
      apiRequest<TaskComment[]>(`/api/tasks/${props.taskId}/comments`),
      apiRequest<TaskNotification[]>(`/api/tasks/${props.taskId}/notifications`),
    ])
    comments.value = Array.isArray(commentList) ? commentList : []
    notifications.value = Array.isArray(notifList) ? notifList : []
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '评论加载失败')
  } finally {
    loading.value = false
  }
}

function formatTime(value: string) {
  if (!value) return ''
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(value))
}

async function publish() {
  const text = body.value.trim()
  if (!text) {
    ElMessage.warning('评论内容不能为空')
    return
  }
  sending.value = true
  try {
    await apiRequest<TaskComment>(`/api/tasks/${props.taskId}/comments`, {
      method: 'POST',
      body: JSON.stringify({ body: text }),
    })
    body.value = ''
    await load()
    ElMessage.success('评论已发表')
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '评论发表失败')
  } finally {
    sending.value = false
  }
}

async function remove(comment: TaskComment) {
  try {
    await apiRequest<TaskComment>(`/api/tasks/${props.taskId}/comments/${comment.id}`, { method: 'DELETE' })
    await load()
    ElMessage.success('评论已删除')
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '删除失败')
  }
}

async function markRead(notification: TaskNotification) {
  try {
    await apiRequest(`/api/notifications/${notification.id}/read`, { method: 'POST' })
    notification.read_at = new Date().toISOString()
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '标记已读失败')
  }
}

async function nudge() {
  nudging.value = true
  try {
    await apiRequest(`/api/tasks/${props.taskId}/nudge`, { method: 'POST' })
    ElMessage.success('催办通知已发送给审批人')
    await load()
  } catch (error) {
    ElMessage.error(error instanceof ApiError ? error.message : '催办失败')
  } finally {
    nudging.value = false
  }
}

onMounted(() => {
  if (props.taskId) {
    void load()
  }
  connectRealtime()
  unsubscribeRealtime = subscribeRealtime((event) => {
    if (event.task_id === props.taskId || (event.resource_type === 'task' && event.resource_id === props.taskId)) {
      void load()
    }
  })
})

onBeforeUnmount(() => {
  unsubscribeRealtime?.()
  unsubscribeRealtime = undefined
})
</script>

<template>
  <section class="panel task-comments">
    <header class="panel__header">
      <div>
        <h2><MessageSquareText :size="17" class="inline-icon" /> 协作与评论</h2>
        <p>围绕任务上下文评论与 @ 成员，可关联计划、Diff、测试记录或回放事件。</p>
      </div>
      <span class="task-comments__hint">使用 @姓名 提及成员</span>
    </header>

    <div class="task-comments__compose">
      <textarea
        v-model="body"
        class="mono"
        rows="3"
        maxlength="2000"
        placeholder="发表评论，@姓名 可提醒成员…"
        @keydown.enter.exact.prevent="publish"
      />
      <footer>
        <button class="button button--primary" :disabled="sending || !body.trim()" @click="publish">
          <Send :size="15" /> 发表评论
        </button>
      </footer>
    </div>

    <div v-if="notifications.length" class="task-comments__notices">
      <h3><Bell :size="15" class="inline-icon" /> 任务通知</h3>
      <article
        v-for="item in notifications"
        :key="item.id"
        class="task-notice"
        :class="{ 'is-high': item.priority === 'high', 'is-read': item.read_at }"
      >
        <span class="task-notice__tag">{{ notificationTypeText[item.type] }}</span>
        <div class="task-notice__body">
          <strong>{{ item.title }}</strong>
          <small>{{ item.body }} · {{ item.actor_name }} · {{ formatTime(item.created_at) }}</small>
        </div>
        <button v-if="!item.read_at" class="button button--ghost button--sm" type="button" @click="markRead(item)">标记已读</button>
      </article>
    </div>

    <div v-if="nudgeEligible" class="task-comments__nudge">
      <strong><BellRing :size="15" class="inline-icon" /> 待审批超过 24 小时未见处理？</strong>
      <button class="button button--secondary" :disabled="nudging" @click="nudge">发送催办通知</button>
    </div>

    <div class="task-comments__list">
      <div v-if="loading" class="task-comments__empty">加载中…</div>
      <div v-else-if="!comments.length" class="task-comments__empty">还没有评论，发表第一条参与协作者。</div>
      <article v-for="comment in comments" :key="comment.id" class="task-comment" :class="{ 'is-deleted': comment.deleted_at }">
        <span class="avatar task-comment__avatar">{{ comment.author_name?.slice(0, 2) ?? '?' }}</span>
        <div class="task-comment__main">
          <header>
            <strong>{{ comment.author_name }}</strong>
            <time>{{ formatTime(comment.created_at) }}</time>
            <em v-if="comment.reference_type">{{ comment.reference_type }} {{ comment.reference_id }}</em>
          </header>
          <p v-if="comment.deleted_at" class="task-comment__deleted">该评论已删除</p>
          <p v-else class="task-comment__body">{{ comment.body }}</p>
          <footer v-if="!comment.deleted_at">
            <button
              v-if="currentUserId === comment.author_id"
              class="button button--ghost button--sm"
              type="button"
              @click="remove(comment)"
            >
              <Trash2 :size="13" /> 删除
            </button>
          </footer>
        </div>
      </article>
    </div>
  </section>
</template>

<style scoped>
.inline-icon {
  vertical-align: -2px;
}
.task-comments {
  overflow: hidden;
}
.task-comments__compose,
.task-comments__notices,
.task-comments__nudge,
.task-comments__list {
  margin-right: 18px;
  margin-left: 18px;
}
.task-comments__compose {
  padding-top: 16px;
}
.task-comments__hint {
  font-size: 12px;
  color: var(--task-faint);
}
.task-comments__compose textarea {
  width: 100%;
  resize: vertical;
}
.task-comments__compose footer {
  margin-top: 10px;
  padding-bottom: 1px;
  display: flex;
  justify-content: flex-end;
}
.task-comments__notices {
  display: grid;
  gap: 8px;
  margin-top: 18px;
}
.task-comments__notices h3 {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--task-text);
  font-size: 13px;
  line-height: 18px;
}
.task-comments__list {
  margin-top: 18px;
  padding-bottom: 18px;
  display: grid;
  gap: 12px;
}
.task-comments__empty {
  padding: 18px;
  color: var(--task-muted);
  text-align: center;
  border: 1px dashed var(--task-border);
  border-radius: 8px;
  font-size: 13px;
}
.task-comment {
  display: flex;
  gap: 10px;
}
.task-comment__avatar {
  flex: none;
}
.task-comment__main {
  flex: 1;
  background: var(--task-surface-subtle);
  border: 1px solid var(--task-border-soft);
  border-radius: 8px;
  padding: 10px 12px;
}
.task-comment__main header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--task-muted);
}
.task-comment__main header strong {
  color: var(--task-text);
}
.task-comment__main header em {
  font-style: normal;
  background: var(--task-surface-muted);
  color: var(--task-muted);
  border-radius: 5px;
  padding: 1px 6px;
}
.task-comment__body {
  margin-top: 4px;
  white-space: pre-wrap;
  word-break: break-word;
}
.task-comment__deleted {
  margin-top: 4px;
  font-style: italic;
  color: var(--task-faint);
}
.task-comment__main footer {
  margin-top: 6px;
  display: flex;
  justify-content: flex-end;
}
.task-notice {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--task-border-soft);
  border-radius: 8px;
  background: var(--notice-bg, var(--task-surface-subtle));
  border-color: var(--notice-border, var(--task-border-soft));
}
.task-notice.is-high {
  --notice-bg: var(--theme-warning-surface);
  --notice-border: var(--theme-warning-border);
  --notice-title: var(--theme-warning-text);
  --notice-meta: var(--theme-warning-muted);
  --notice-tag-bg: var(--theme-warning-tag);
  --notice-tag-text: var(--theme-warning-text);
  box-shadow: inset 0 0 0 1px var(--theme-warning-border);
}
.task-notice.is-read {
  --notice-bg: var(--task-surface-muted);
  --notice-border: var(--task-border-soft);
  --notice-title: var(--task-muted);
  --notice-meta: var(--task-faint);
  --notice-tag-bg: var(--task-surface-subtle);
  --notice-tag-text: var(--task-faint);
}
.task-notice.is-high.is-read {
  --notice-bg: var(--theme-warning-read-surface);
  --notice-border: var(--theme-warning-read-border);
  --notice-title: var(--theme-warning-read-text);
  --notice-meta: var(--theme-warning-read-muted);
  --notice-tag-bg: var(--theme-warning-read-tag);
  --notice-tag-text: var(--theme-warning-read-text);
  box-shadow: inset 0 0 0 1px var(--theme-warning-read-border);
}
.task-notice__tag {
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 999px;
  color: var(--notice-tag-text, var(--task-muted));
  background: var(--notice-tag-bg, var(--task-surface-muted));
  flex: none;
}
.task-notice__body {
  flex: 1;
  min-width: 0;
  display: grid;
  gap: 2px;
}
.task-notice__body strong {
  font-size: 13px;
  color: var(--notice-title, var(--task-text));
}
.task-notice__body small {
  font-size: 12px;
  color: var(--notice-meta, var(--task-muted));
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.task-comments__nudge {
  --nudge-bg: var(--theme-warning-surface);
  --nudge-border: var(--theme-warning-border);
  --nudge-text: var(--theme-warning-text);
  margin-top: 18px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  border-radius: 8px;
  background: var(--nudge-bg);
  border: 1px solid var(--nudge-border);
}
.task-comments__nudge strong {
  font-size: 13px;
  color: var(--nudge-text);
}

@media (max-width: 640px) {
  .task-comments__compose,
  .task-comments__notices,
  .task-comments__nudge,
  .task-comments__list {
    margin-right: 14px;
    margin-left: 14px;
  }
  .task-comments__notices {
    margin-top: 16px;
  }
  .task-comments__list {
    margin-top: 16px;
    padding-bottom: 14px;
  }
  .task-notice {
    align-items: flex-start;
    flex-wrap: wrap;
  }
  .task-notice__body {
    flex-basis: calc(100% - 42px);
  }
  .task-notice .button {
    margin-left: 30px;
  }
  .task-comments__nudge {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>
