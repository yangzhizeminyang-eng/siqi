import { describe, expect, it } from 'vitest'
import { summarizeFailureReason } from '../failureReason'

describe('summarizeFailureReason', () => {
  it('turns repeated test failures into an actionable summary', () => {
    expect(summarizeFailureReason('测试连续失败 12 次（上限 2）：WARNING: Running pip as the root user; exit 2')).toBe(
      '测试连续失败 12 次（上限 2），退出码 2。完整失败输出已保留在测试记录中。',
    )
  })

  it('uses a known exit code when the raw reason omits it', () => {
    expect(summarizeFailureReason('测试连续失败 12 次（上限 2）：WARNING: Running pip as the root user', 2)).toBe(
      '测试连续失败 12 次（上限 2），退出码 2。完整失败输出已保留在测试记录中。',
    )
  })

  it('keeps short domain errors intact', () => {
    expect(summarizeFailureReason('Issue 信息过少。')).toBe('Issue 信息过少。')
  })

  it('provides an evidence fallback for empty reasons', () => {
    expect(summarizeFailureReason(null)).toBe('未记录失败原因')
  })
})
