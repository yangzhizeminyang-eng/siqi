import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { setDataSourceMode } from '../dataSourceMode'
import {
  connectRealtime,
  disconnectRealtime,
  subscribeRealtime,
  type TeamRealtimeEvent,
} from '../realtime'

type EventHandler = (event: MessageEvent<string>) => void

class MockEventSource {
  static instances: MockEventSource[] = []

  readonly url: string
  readonly options: EventSourceInit
  closed = false
  private readonly handlers = new Map<string, EventHandler[]>()

  constructor(url: string, options?: EventSourceInit) {
    this.url = url
    this.options = options ?? {}
    MockEventSource.instances.push(this)
  }

  addEventListener(type: string, listener: EventListenerOrEventListenerObject): void {
    const handler = typeof listener === 'function'
      ? listener as EventHandler
      : (event: MessageEvent<string>) => listener.handleEvent(event)
    this.handlers.set(type, [...(this.handlers.get(type) ?? []), handler])
  }

  close(): void {
    this.closed = true
  }

  emit(type: string, data: string): void {
    const event = new MessageEvent<string>(type, { data })
    this.handlers.get(type)?.forEach((handler) => handler(event))
  }
}

describe('realtime：团队版 SSE 连接', () => {
  beforeEach(() => {
    setDataSourceMode('real')
    MockEventSource.instances = []
    vi.stubGlobal('EventSource', MockEventSource)
  })

  afterEach(() => {
    disconnectRealtime()
    vi.unstubAllGlobals()
    setDataSourceMode('real')
  })

  it('重复连接只建立一条 EventSource，并使用登录态请求', () => {
    connectRealtime()
    connectRealtime()

    expect(MockEventSource.instances).toHaveLength(1)
    expect(MockEventSource.instances[0].url).toBe('/api/events/stream')
    expect(MockEventSource.instances[0].options).toEqual({ withCredentials: true })
  })

  it('收到 team-update 后通知监听器，并忽略非法 JSON', () => {
    const listener = vi.fn<(event: TeamRealtimeEvent) => void>()
    const unsubscribe = subscribeRealtime(listener)
    connectRealtime()
    const source = MockEventSource.instances[0]

    source.emit('team-update', JSON.stringify({ type: 'task.updated', task_id: 'TASK-1' }))
    source.emit('team-update', '{broken-json')

    expect(listener).toHaveBeenCalledTimes(1)
    expect(listener).toHaveBeenCalledWith({ type: 'task.updated', task_id: 'TASK-1' })
    unsubscribe()
  })

  it('取消订阅后不再收到事件', () => {
    const listener = vi.fn<(event: TeamRealtimeEvent) => void>()
    const unsubscribe = subscribeRealtime(listener)
    connectRealtime()
    unsubscribe()

    MockEventSource.instances[0].emit('team-update', JSON.stringify({ type: 'comment.updated' }))

    expect(listener).not.toHaveBeenCalled()
  })

  it('mock 模式不建立 SSE 连接', () => {
    setDataSourceMode('mock')

    connectRealtime()

    expect(MockEventSource.instances).toHaveLength(0)
  })

  it('断开连接会关闭 EventSource 并清空监听器', () => {
    const listener = vi.fn<(event: TeamRealtimeEvent) => void>()
    subscribeRealtime(listener)
    connectRealtime()
    const source = MockEventSource.instances[0]

    disconnectRealtime()
    source.emit('team-update', JSON.stringify({ type: 'task.updated' }))

    expect(source.closed).toBe(true)
    expect(listener).not.toHaveBeenCalled()
  })
})
