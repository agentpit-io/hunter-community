# nginx 配置

线上 nginx 配置**原本不在仓库里**,只存在于服务器的
`/etc/nginx/sites-enabled/hunter-community`。这带来两个问题:

1. 改了没人知道改了什么、什么时候改的
2. 换台机器部署要靠人回忆

`hunter-community.conf` 是线上那份的对照副本。改配置的流程:

```bash
# 1. 本地改这个文件 · commit · push
# 2. 服务器上
sudo cp ~/hunter-community/deploy/nginx/hunter-community.conf \
        /etc/nginx/sites-available/hunter-community
sudo nginx -t                    # ← 必须先校验,配置错了 reload 会让全站 502
sudo systemctl reload nginx
```

> ⚠️ certbot 会往这个文件里注入 443 server 块和 301 跳转。
> 拷贝覆盖前先 `diff` 一遍,别把证书配置洗掉。
> 更稳的做法是只改需要的那几行(见 `client_max_body_size` 那条注释)。
