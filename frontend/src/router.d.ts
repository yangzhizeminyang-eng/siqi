import 'vue-router'
import type { AccessRequirement } from './services/accessControl'

declare module 'vue-router' {
  interface RouteMeta {
    public?: boolean
    title?: string
    layout?: 'blank'
    permission?: AccessRequirement
  }
}
