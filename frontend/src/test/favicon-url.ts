// vitest 专用：plugin-vue 会把模板里静态 <img src="/favicon.svg"> 编译为资源 import，
// module runner 无法解析 public 目录路径（file:///favicon.svg 非法），此处映射为普通 JS 模块。
export default '/favicon.svg'
