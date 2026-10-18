# 小家的后端

`xiaojia.py` 一个文件，只用 Python 自带的库。现在做的是**第一步**：

- 把 `web/` 的页面放出去，手机电脑随时能开
- 门锁：没输密码只能看到登录页
- 待办、日历（日程 + 心情）、留言板、悄悄话、tt 的日记，真的存下来
- 小克要写的那几页、聊天，先给空的——等**第二步**接上 Claude 再填

数据在 `data/`（服务器上是 `~/xiaojia/data/`），一个功能一个 JSON 文件，不进 git。

## 服务器上的样子

```
~/xiaojia/
  app/          web/ 和 server/，从电脑上传过来
  data/         待办、日历……还有门锁（auth.json）
  ngrok.yml     第二个 ngrok 账号的 token（自己填的，别人看不到）
```

两个开机自启的服务：

| 服务 | 干什么 |
|---|---|
| `xiaojia` | 后端，只听本机 127.0.0.1:8090 |
| `xiaojia-ngrok` | 用第二个 ngrok 账号把 8090 变成 https 地址 |

记忆库那条 ngrok（`ngrok.service`，转 18001）**不碰**。

## 第一次装

```bash
# 1. 电脑上：把页面和后端传上去（在仓库根目录）
tar czf - web server | ssh ubuntu@服务器 'mkdir -p ~/xiaojia/app && tar xzf - -C ~/xiaojia/app'

# 2. 服务器上：装两个服务
sudo cp ~/xiaojia/app/server/xiaojia.service ~/xiaojia/app/server/xiaojia-ngrok.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now xiaojia

# 3. 服务器上，自己来（token 和密码都只在这一步出现）
/snap/bin/ngrok config add-authtoken 你的token --config /home/ubuntu/xiaojia/ngrok.yml
XIAOJIA_DATA=~/xiaojia/data python3 ~/xiaojia/app/server/xiaojia.py --set-password
sudo systemctl enable --now xiaojia-ngrok
```

## 以后更新

```bash
tar czf - web server | ssh ubuntu@服务器 'tar xzf - -C ~/xiaojia/app && sudo systemctl restart xiaojia'
```

页面改了不用手动刷新：开着的页面发现 `index.html` 变了会自己重新载入。

## 改密码 / 看状态

```bash
XIAOJIA_DATA=~/xiaojia/data python3 ~/xiaojia/app/server/xiaojia.py --set-password   # 改完所有设备都要重新登录
systemctl status xiaojia xiaojia-ngrok
journalctl -u xiaojia -n 50
```

## 省着用 ngrok

免费账号一个月 2 万次请求、1 GB 流量。所以：页面 gzip 压缩，没变的文件回 304，
聊天那条长轮询没消息就挂两分钟，页面在后台就不问。用量在 ngrok 网站的 Usage 页能看。
