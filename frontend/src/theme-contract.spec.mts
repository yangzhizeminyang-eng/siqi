import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

function frontendFile(path: string) {
  const direct = resolve(process.cwd(), path)
  return existsSync(direct) ? direct : resolve(process.cwd(), 'frontend', path)
}

function source(path: string) {
  return readFileSync(frontendFile(path), 'utf8')
}

describe('任务详情主题契约', () => {
  it('全局样式为浅色和深色分别提供同一组语义 token', () => {
    const css = source('src/style.css')

    for (const token of [
      '--theme-surface-base',
      '--theme-surface-subtle',
      '--theme-surface-muted',
      '--theme-border',
      '--theme-text',
      '--theme-text-muted',
      '--theme-link',
      '--theme-warning-surface',
    ]) {
      expect(css.split(token).length - 1).toBeGreaterThanOrEqual(2)
    }
  })

  it('任务详情链路组件不自行分支深浅主题', () => {
    for (const path of [
      'src/views/TaskDetailView.vue',
      'src/components/ModificationPlanPanel.vue',
      'src/components/CodeContextPanel.vue',
      'src/components/TaskCommentsPanel.vue',
      'src/components/HistoricalTasksPanel.vue',
    ]) {
      expect(source(path)).not.toContain('data-theme')
    }
  })

  it('任务详情不再依赖浅色 !important 兜底或深色命令中心覆写', () => {
    const css = source('src/style.css')

    expect(css).not.toContain('Light theme guard: task detail')
    expect(css).not.toContain('[data-theme="dark"] .task-detail .task-command')
    expect(css).not.toContain(':root:not([data-theme="dark"]) .task-detail')
  })
})

describe('登录页主题契约', () => {
  it('登录页左右两栏共享同一套浅深主题 token', () => {
    const css = source('src/style.css')

    for (const token of [
      '--login-page-bg',
      '--login-form-bg',
      '--login-aside-bg',
      '--login-aside-text',
      '--login-aside-muted',
      '--login-aside-eyebrow',
      '--login-aside-node-active',
    ]) {
      expect(css.split(token).length - 1).toBeGreaterThanOrEqual(2)
    }
  })

  it('登录页左侧品牌区不再硬编码单一深色外观', () => {
    const css = source('src/style.css')

    expect(css).toMatch(/\.login-aside\s*\{[^}]*color:\s*var\(--login-aside-text\)[^}]*background:\s*var\(--login-aside-bg\)/s)
    expect(css).toMatch(/\.login-aside::before\s*\{[^}]*background:\s*var\(--login-aside-glow\)/s)
    expect(css).toMatch(/\.pipeline-node\.is-active i\s*\{[^}]*background:\s*var\(--login-aside-node-active\)/s)
    expect(css).toContain('.login-aside h2')
  })
})
