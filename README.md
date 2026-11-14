# Rime → 水杉词库桥接

把 Windows 小狼毫导出的个人拼音词条单向同步到水杉。Windows 通过 SSH 把词条交给 Linux 上的桥接程序，桥接程序使用水杉自己的导入及加密 WebDAV 同步接口。iPhone 在水杉里加入同一套词库即可接收。

需要 PowerShell 7、OpenSSH、Linux 上的 Python 3 和一个可写的 WebDAV 目录。Linux 端使用上游 [水杉发布包](https://github.com/metasequoiaime/msime/releases)中的 `libmsime_host_api.so`。仓库只提供桥接代码。它依赖上游当前的接口和拼音表结构，更新水杉组件后应重新验证。

1. 将 `bridge.py` 放到 Linux 用户的 `~/rime-msime-bridge/`。将上游与机器架构匹配的库提取到 `~/.local/state/rime-msime-bridge/libmsime_host_api.so`。目录权限设为 `700`。如需换位置，可在 Linux 端设置 `MSIME_HOST_LIBRARY` 和 `RIME_MSIME_STATE_DIR`。
2. 在同一个状态目录建权限为 `600` 的 `webdav-connection.json`，格式为 `{"url":"https://dav.example.org/dictionary/","username":"your-user","password":"your-password"}`。首次部署建议用一个空的 WebDAV 子目录。运行 `python3 ~/rime-msime-bridge/bridge.py configure`，再运行同一路径下的 `bridge.py run` 初始化。如果加入已有词库，先由已批准设备核对并批准桥接设备，再运行同步。
3. 在 Windows 运行下方命令。替换路径、SSH 主机和密钥。已导出快照时可省略 `-Deployer`，否则脚本会先运行小狼毫的 `/sync`。验证一次成功后，可用 Windows 任务计划程序在当前用户登录时定期运行同一命令。

```powershell
pwsh -NoProfile -File .\sync-rime.ps1 `
  -Snapshot 'C:\Rime\sync\DEVICE-ID\rime_ice.userdb.txt' `
  -Deployer 'C:\Rime\WeaselDeployer.exe' `
  -SshHost 'rime-user@nas.example.org' `
  -IdentityFile 'C:\Keys\rime-nas'
```

4. iPhone 水杉打开「词库同步」，填同一个 HTTPS WebDAV 地址。Linux 端运行 `bridge.py devices` 查看配对请求，对照两边的核对码，再运行 `bridge.py approve DEVICE_ID`。最后在手机点「立即同步」。

首次运行发送所有符合格式的词。后续仅追加未见过的词和读音。Rime 的删词、学习排序和水杉里的改动不回传。脚本跳过非纯汉字词和非全拼编码。水杉拒收的条数会显示在结果中。

### 双向追加（可选）

把 `merge-rime.ps1` 和 `sync-rime.ps1` 放在同一目录，在上述 Windows 命令末尾加 `-TwoWay`。请指定 `-Deployer` 和雾凇拼音的 `rime_ice.userdb.txt`，并保持 Rime 默认的设备快照目录结构。脚本先发布 Rime 新词，再把水杉里电脑上还没有的全拼词并入 Rime。合并走小狼毫的用户资料同步，成功后用新快照核对。现有词条和使用次数保留，双向追加不传播删除和排序。已有的 Rime 删除记录也会阻止同一词条被重新加回。

双向模式需要每次检查 WebDAV。遇到网盘限流时，等待额度恢复后再运行。请让同一个状态目录服务同一台 Windows 电脑，并避免同时运行多个同步任务。

桥接脚本使用 GPL-3.0 许可证。Rime 快照格式见 [Rime 文档](https://github.com/rime/home/wiki/UserGuide)，同步协议见 [水杉 WebDAV 文档](https://github.com/metasequoiaime/msime/blob/develop/docs/dictionary-webdav-sync.md)。
