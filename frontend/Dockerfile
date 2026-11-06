FROM node:22-bookworm-slim AS builder

ARG VITE_DATA_SOURCE=real
ENV VITE_DATA_SOURCE=${VITE_DATA_SOURCE} \
    NODE_OPTIONS=--max-old-space-size=768

WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

FROM nginx:1.29-alpine
# 按构建目标选择站点配置：team 版默认 nginx.team.conf；企业版传 nginx.enterprise.conf
ARG NGINX_CONF=nginx.team.conf
# 主配置为「非 root 兼容」：移除 setuid 指令、pid 落 /tmp、临时/缓存目录落 /tmp/nginx_* 并 chown
COPY deploy/nginx.master.conf /etc/nginx/nginx.conf
COPY deploy/${NGINX_CONF} /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html

# 非 root：创建可写临时/缓存目录并授权 nginx 用户；不滥用宽目录权限、不提升特权或能力。
# 静态资源目录授权 nginx（供其读取/服务）；/etc/nginx 保持 root 只读（nginx 仅需读站点配置，无需写）。
RUN mkdir -p /tmp/nginx_client /tmp/nginx_proxy /tmp/nginx_fastcgi /tmp/nginx_uwsgi /tmp/nginx_scgi \
    && chown -R nginx:nginx /tmp/nginx_client /tmp/nginx_proxy /tmp/nginx_fastcgi /tmp/nginx_uwsgi /tmp/nginx_scgi \
    && chown -R nginx:nginx /usr/share/nginx/html

USER nginx
EXPOSE 8080
