import { getDataSourceMode } from './dataSourceMode'

export interface TeamRealtimeEvent {
  type: string
  resource_type?: string
  resource_id?: string
  task_id?: string | null
  notification_type?: string
  updated_at?: string
  occurred_at?: string
  reason?: string
}

type RealtimeListener = (event: TeamRealtimeEvent) => void

let eventSource: EventSource | null = null
const listeners = new Set<RealtimeListener>()

function handleMessage(message: MessageEvent<string>) {
  try {
    const event = JSON.parse(message.data) as TeamRealtimeEvent
    if (event && typeof event.type === 'string') {
      listeners.forEach((listener) => listener(event))
    }
  } catch {
    // SSE payloads are advisory; malformed events must not interrupt the UI connection.
  }
}

/**
 * 函数级注释：建立全局团队版 SSE 连接。
 * 入参：无。
 * 返回值：void；重复调用复用已有连接，mock 模式或不支持 EventSource 时空操作。
 * 异常：浏览器负责断线自动重连；业务数据读取失败由各页面自行兜底。
 */
export function connectRealtime(): void {
  if (getDataSourceMode() !== 'real' || typeof window === 'undefined' || typeof EventSource === 'undefined') return
  if (eventSource) return
  eventSource = new EventSource('/api/events/stream', { withCredentials: true })
  eventSource.addEventListener('team-update', handleMessage as EventListener)
}

/**
 * 函数级注释：订阅实时资源变化事件。
 * 入参：listener 事件回调。
 * 返回值：取消订阅函数。
 */
export function subscribeRealtime(listener: RealtimeListener): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/**
 * 函数级注释：关闭当前浏览器会话的 SSE 连接并清空监听器。
 * 入参：无。
 * 返回值：void。
 */
export function disconnectRealtime(): void {
  eventSource?.close()
  eventSource = null
  listeners.clear()
}
