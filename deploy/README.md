# 云端端口预留

这套文件保留多个版本的端口规划；当前团队版 Docker Compose 实际使用 `8080`，避免占用服务器上已有的 `8002` 服务：

| 版本 | 公网端口 | 说明 |
| --- | --- | --- |
| MVP | `8001` | 第一版对外入口 |
| 团队版 | `8080` | `deploy/docker-compose.team.yml` 的 Nginx 入口 |
| 企业版 | `8081` | `deploy/docker-compose.enterprise.yml` 的 Nginx 入口（默认 `${ENTERPRISE_PUBLIC_PORT:-8081}`） |

约定：

- 同一份代码，启动 3 套独立实例。
- 旧的端口规划 `8001 / 8002 / 8003` 仅供历史进程参考，不代表当前团队版 Compose 入口。
- 容器或进程内部端口可以统一使用 `8000`。
- 后续上线时，每个版本再分别绑定自己的数据库、Redis、Milvus 和密钥配置。

相关文件：

- [docker-compose.ports.example.yml](./docker-compose.ports.example.yml)
- [env/mvp.env.example](./env/mvp.env.example)
- [env/team.env.example](./env/team.env.example)
- [env/enterprise.env.example](./env/enterprise.env.example)
- [团队版云部署开发记录](../docs/project-development/96-team-cloud-deployment.md)

## 企业版域名 HTTPS（E6-01）

正式环境不要让企业 frontend 直接占用公网 `8081`。在宿主机安装 Nginx，
将真实证书放在服务器受保护路径，然后叠加 host TLS override：

```bash
sudo apt-get update
sudo apt-get install -y nginx openssl ca-certificates

export E6_DOMAIN=agentforge.ltd
export E6_CERT_SOURCE=/secure/path/agentforge.ltd_bundle.crt
export E6_KEY_SOURCE=/secure/path/agentforge.ltd.key
sudo -E bash deploy/scripts/configure-host-https.sh
```

脚本会校验证书与私钥匹配、安装证书（私钥 `0600`）、写入
`/etc/nginx/conf.d/agentforge.ltd.tls.conf`、执行 `nginx -t`，并使用
`docker-compose.enterprise.host-tls.yml` 将企业 frontend 绑定到
`127.0.0.1:8081`。证书私钥不进入 Git、不写入镜像。

如果使用完整企业栈而不是应用-only 栈：

```bash
export E6_COMPOSE_FILE=/srv/enterprise/deploy/docker-compose.enterprise.yml
sudo -E bash deploy/scripts/configure-host-https.sh
```

验证：

```bash
bash deploy/scripts/verify-dns.sh agentforge.ltd
bash deploy/scripts/verify-https.sh agentforge.ltd
curl -I http://agentforge.ltd
curl -fsS https://agentforge.ltd/api/health
```

预期是 HTTP `308` 跳转、HTTPS TLS 链校验通过、`/api/health` 返回 `200`。
