const REPEATED_TEST_FAILURE = /^(测试连续失败\s*\d+\s*次\s*[（(]上限\s*\d+\s*[）)])/u
const EXIT_CODE = /\bexit(?:\s*code)?\s*[=:]?\s*(-?\d+)\b/i

/** Keep the decision signal visible while leaving the complete runner output in evidence views. */
export function summarizeFailureReason(reason: string | null | undefined, knownExitCode?: number | string | null): string {
  const value = reason?.replace(/\s+/g, ' ').trim()
  if (!value) return '未记录失败原因'

  const repeatedTestFailure = value.match(REPEATED_TEST_FAILURE)?.[1]
  const warningIndex = value.search(/\bWARNING:\s*Running pip\b/i)
  const exitCode = value.match(EXIT_CODE)?.[1] ?? (knownExitCode === null || knownExitCode === undefined ? '' : String(knownExitCode))

  if (repeatedTestFailure) {
    return `${repeatedTestFailure}${exitCode ? `，退出码 ${exitCode}` : ''}。完整失败输出已保留在测试记录中。`
  }

  if (warningIndex >= 0) {
    const prefix = value.slice(0, warningIndex).replace(/[：:]\s*$/, '').trim()
    return `${prefix || '测试命令执行失败'}${exitCode ? `，退出码 ${exitCode}` : ''}。完整失败输出已保留在测试记录中。`
  }

  if (value.length <= 180) return value
  const clipped = value.slice(0, 160).replace(/\s+\S*$/, '').trim()
  return `${clipped}…完整失败输出已保留在测试记录中。`
}
