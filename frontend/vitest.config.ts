import { fileURLToPath, URL } from 'node:url'
import { defineConfig, mergeConfig } from 'vitest/config'
import viteConfig from './vite.config.ts'

export default mergeConfig(
  viteConfig,
  defineConfig({
    resolve: {
      alias: {
        // plugin-vue 会把静态 img src 编译为资源 import，module runner 解析不了 public 下的绝对路径
        '/favicon.svg': fileURLToPath(new URL('./src/test/favicon-url.ts', import.meta.url)),
      },
    },
    test: {
      environment: 'jsdom',
      include: ['src/**/*.spec.ts', 'src/**/*.spec.mts'],
      pool: 'vmThreads',
      testTimeout: 20000,
    },
  }),
)
